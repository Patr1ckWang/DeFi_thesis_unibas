-- user v5
-- time frame: 2020-05-18 ~ 2020-09-09
-- attack date: 2020-08-26
-- classification: cohort (Old/New/Early Leaver/Returner) × faction (Migrator/Fence-sitter/Stayer/Runaway)


WITH pool_list AS (
  SELECT address, name, migration_block FROM UNNEST([
    STRUCT('0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc' AS address, 'USDC-WETH' AS name, 10829331 AS migration_block),
    STRUCT('0x0d4a11d5eeaac28ec3f61d100daf4d40471f1852' AS address, 'USDT-WETH' AS name, 10829344 AS migration_block),
    STRUCT('0xa478c2975ab1ea89e8196811f51a7b7ade33eb11' AS address, 'DAI-WETH'  AS name, 10829331 AS migration_block),
    STRUCT('0xcffdded873554f362ac02f8fb1f02e5ada10516f' AS address, 'COMP-WETH' AS name, 10829250 AS migration_block),
    STRUCT('0xa2107fa5b38d9bbd2c461d6edf11b11a50f6b974' AS address, 'LINK-WETH' AS name, 10829307 AS migration_block),
    STRUCT('0x2fdbadf3c4d5a8666bc06645b8358ab803996e28' AS address, 'YFI-WETH'  AS name, 10829310 AS migration_block)
  ])
),

masterchef AS (
  SELECT '0xc2edad668740f1aa35e4d8f227fb8e17dca888cd' AS address
),

raw_events AS (
  SELECT
    t.token_address                             AS pool_address,
    t.from_address,
    t.to_address,
    CAST(t.value AS BIGNUMERIC) / 1e18          AS val,
    t.block_timestamp
  FROM `bigquery-public-data.crypto_ethereum.token_transfers` t
  JOIN pool_list p ON t.token_address = p.address
  WHERE t.block_timestamp >= TIMESTAMP('2020-05-18 00:00:00') 
    AND t.block_timestamp <  TIMESTAMP('2020-09-11 00:00:00') -- 放宽时间戳，依赖区块高度截断
    AND t.block_number <= p.migration_block -- 精确到每个池子的创世区块
),

-- 构造用户借贷分录。不排除 MasterChef，让它出现在 counterparty 列。只排除零地址（mint = from 0x0, burn = to 0x0）
user_ledgers AS (
  -- 用户接收 LP token（mint / 收到转账 / 从 SushiSwap 撤回）
  SELECT
    to_address     AS user_address,
    pool_address,
    val            AS amount,
    block_timestamp,
    from_address   AS counterparty
  FROM raw_events
  WHERE to_address != '0x0000000000000000000000000000000000000000'

  UNION ALL

  -- 用户转出 LP token（burn / 转给别人 / 存入 SushiSwap）
  SELECT
    from_address   AS user_address,
    pool_address,
    -val           AS amount,
    block_timestamp,
    to_address     AS counterparty
  FROM raw_events
  WHERE from_address != '0x0000000000000000000000000000000000000000'
),

user_aggregation AS (
  SELECT
    user_address,
    pool_address,

    -- [A] 8/26 前的累计净余额
    SUM(CASE WHEN block_timestamp < TIMESTAMP('2020-08-26')
             THEN CAST(amount AS FLOAT64) ELSE 0 END)
      AS balance_pre_0826,

    -- [B] 8/26 前是否曾有过正向流入
    MAX(CASE WHEN block_timestamp < TIMESTAMP('2020-08-26')
                  AND amount > 0
             THEN 1 ELSE 0 END)
      AS had_inflow_pre_0826,

    -- [C] 首次流入时间
    MIN(CASE WHEN amount > 0 THEN block_timestamp END)
      AS first_entry_date,

    -- [D] 8/26 前最后一次导致余额减少的时间（用于计算持有时长）
    MAX(CASE WHEN block_timestamp < TIMESTAMP('2020-08-26')
                  AND amount < 0
             THEN block_timestamp END)
      AS last_outflow_pre_0826,

    -- [E] 8/26 前首次流入时间（用于计算最小持有时长）
    MIN(CASE WHEN block_timestamp < TIMESTAMP('2020-08-26')
                  AND amount > 0
             THEN block_timestamp END)
      AS first_inflow_pre_0826,

    -- [F] 全周期累计净余额（截至 9/9）
    SUM(CAST(amount AS FLOAT64))
      AS balance_0909,

    -- [G] 净质押到 SushiSwap 的 LP token 量
    --     当 counterparty = MasterChef 且 amount < 0 → 用户转出给MC（质押）→ 取反变正
    --     当 counterparty = MasterChef 且 amount > 0 → 用户从MC收回（撤出）→ 取反变负
    SUM(CASE WHEN counterparty = (SELECT address FROM masterchef)
             THEN -CAST(amount AS FLOAT64)
             ELSE 0 END)
      AS staked_sushi_0909,

    -- [H] 8/26 之后的正向流入量
    SUM(CASE WHEN block_timestamp >= TIMESTAMP('2020-08-26')
                  AND amount > 0
             THEN CAST(amount AS FLOAT64) ELSE 0 END)
      AS inflow_0826_0909

  FROM user_ledgers
  -- 排除池合约地址本身和 MasterChef 作为"用户"
  WHERE user_address NOT IN (SELECT address FROM pool_list)
    AND user_address != (SELECT address FROM masterchef)
    AND user_address != '0x0000000000000000000000000000000000000000'
  GROUP BY 1, 2
),

user_classification AS (
  SELECT
    user_address,
    pool_address,
    GREATEST(balance_pre_0826, 0)   AS balance_pre_0826,
    GREATEST(balance_0909, 0)       AS balance_0909,
    GREATEST(staked_sushi_0909, 0)  AS staked_sushi_0909,
    first_entry_date,

    -- Cohort
    CASE
      -- 8/26 前有流入，但 8/26 时余额已清零
      WHEN had_inflow_pre_0826 = 1 AND balance_pre_0826 <= 1e-6 THEN
        CASE
          -- 检查最小持有时长 >= 7 天
          WHEN TIMESTAMP_DIFF(
                 COALESCE(last_outflow_pre_0826, first_inflow_pre_0826),
                 first_inflow_pre_0826,
                 DAY) < 7
            THEN 'Ghost'  -- 持有不足 7 天，视为噪音
          -- 之后有回来
          WHEN inflow_0826_0909 > 1e-6 THEN 'Returner'
          ELSE 'Early Leaver'
        END
      -- 8/26 前有正余额
      WHEN balance_pre_0826 > 1e-6 THEN 'Old'
      -- 8/26 之后才有流入
      WHEN inflow_0826_0909 > 1e-6 THEN 'New'
      -- 从未有过有意义的活动
      ELSE 'Ghost'
    END AS cohort,

    -- Faction
    CASE
      -- Early Leaver 没有派系（在攻击前就走了）
      WHEN had_inflow_pre_0826 = 1 AND balance_pre_0826 <= 1e-6
           AND inflow_0826_0909 <= 1e-6
        THEN 'N/A'
      -- 持有不足 7 天的 Ghost 也无派系
      WHEN had_inflow_pre_0826 = 1 AND balance_pre_0826 <= 1e-6
           AND TIMESTAMP_DIFF(
                 COALESCE(last_outflow_pre_0826, first_inflow_pre_0826),
                 first_inflow_pre_0826,
                 DAY) < 7
        THEN 'N/A'
      -- 正常派系判定
      WHEN staked_sushi_0909 > 1e-6 AND staked_sushi_0909 > balance_0909
        THEN 'Migrator'
      WHEN staked_sushi_0909 > 1e-6 AND staked_sushi_0909 <= balance_0909
        THEN 'Fence-sitter'
      WHEN staked_sushi_0909 <= 1e-6 AND balance_0909 > 1e-6
        THEN 'Stayer'
      WHEN staked_sushi_0909 <= 1e-6 AND balance_0909 <= 1e-6
        THEN 'Runaway'
      ELSE 'Anomaly'
    END AS faction

  FROM user_aggregation
)

SELECT
  uc.user_address,
  pl.name                                       AS pool_name,
  uc.cohort,
  uc.faction,
  CASE 
    WHEN uc.faction = 'N/A' THEN uc.cohort
    ELSE CONCAT(uc.cohort, ' ', uc.faction)
  END                                            AS user_type,
  uc.balance_pre_0826,
  uc.balance_0909,
  uc.staked_sushi_0909,
  uc.first_entry_date
FROM user_classification uc
JOIN pool_list pl ON uc.pool_address = pl.address
WHERE uc.cohort != 'Ghost'
  AND uc.faction != 'Anomaly'
ORDER BY pool_name, cohort, faction, balance_pre_0826 DESC;