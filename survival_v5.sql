
-- 双轨transaction记录
-- 轨道A: Uniswap LP token 事件（6 个池，所有用户）
-- 轨道B: SushiSwap SLP token 事件（6 个池，所有用户）
-- 查询窗口: 2020-09-09 ~ 2021-05-04
-- 输入: users1-defi.users_v5.1

-- 地址定义 
WITH uni_pools AS (
  SELECT address, name, migration_block FROM UNNEST([
    STRUCT('0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc' AS address, 'USDC-WETH' AS name, 10829331 AS migration_block),
    STRUCT('0x0d4a11d5eeaac28ec3f61d100daf4d40471f1852' AS address, 'USDT-WETH' AS name, 10829344 AS migration_block),
    STRUCT('0xa478c2975ab1ea89e8196811f51a7b7ade33eb11' AS address, 'DAI-WETH'  AS name, 10829331 AS migration_block),
    STRUCT('0xcffdded873554f362ac02f8fb1f02e5ada10516f' AS address, 'COMP-WETH' AS name, 10829250 AS migration_block),
    STRUCT('0xa2107fa5b38d9bbd2c461d6edf11b11a50f6b974' AS address, 'LINK-WETH' AS name, 10829307 AS migration_block),
    STRUCT('0x2fdbadf3c4d5a8666bc06645b8358ab803996e28' AS address, 'YFI-WETH'  AS name, 10829310 AS migration_block)
  ])
),

sushi_pools AS (
  SELECT address, name, migration_block FROM UNNEST([
    STRUCT('0x397ff1542f962076d0bfe58ea045ffa2d347aca0' AS address, 'USDC-WETH' AS name, 10829331 AS migration_block),
    STRUCT('0x06da0fd433c1a5d7a4faa01111c044910a184553' AS address, 'USDT-WETH' AS name, 10829344 AS migration_block),
    STRUCT('0xc3d03e4f041fd4cd388c549ee2a29a9e5075882f' AS address, 'DAI-WETH'  AS name, 10829331 AS migration_block),
    STRUCT('0x31503dcb60119a812fee820bb7042752019f2355' AS address, 'COMP-WETH' AS name, 10829250 AS migration_block),
    STRUCT('0xc40d16476380e4037e6b1a2594caf6a6cc8da967' AS address, 'LINK-WETH' AS name, 10829307 AS migration_block),
    STRUCT('0x088ee5007c98a9677165d78dd2109ae4a3d04d0c' AS address, 'YFI-WETH'  AS name, 10829310 AS migration_block)
  ])
),

-- 所有需要追踪的 token 地址（Uni LP + Sushi SLP）
all_tracked_tokens AS (
  SELECT address, migration_block FROM uni_pools
  UNION DISTINCT
  SELECT address, migration_block FROM sushi_pools
),

masterchef AS (
  SELECT '0xc2edad668740f1aa35e4d8f227fb8e17dca888cd' AS address
),

-- Yield Aggregator Vaults 白名单（Harvest + Pickle）
yield_aggregators AS (
  SELECT address, label FROM UNNEST([
    -- Harvest Finance (Uniswap LP - 旧版, 2020-09-17 ~ 迁移前)
    STRUCT('0x1a9f22b4c385f78650e7874d64e442839dc32327' AS address, 'harvest_old' AS label),  -- DAI-WETH
    STRUCT('0x63671425ef4d25ec2b12c7d05de855c143f16e3b' AS address, 'harvest_old' AS label),  -- USDC-WETH
    STRUCT('0xb19ebfb37a936cce783142955d39ca70aa29d43c' AS address, 'harvest_old' AS label),  -- USDT-WETH
    -- Harvest Finance (Uniswap LP - 新版, 迁移后)
    STRUCT('0x307e2752e8b8a9c29005001be66b1c012ca9cdb7' AS address, 'harvest_new' AS label),  -- DAI-WETH
    STRUCT('0xa79a083fdd87f73c2f983c5551ec974685d6bb36' AS address, 'harvest_new' AS label),  -- USDC-WETH
    STRUCT('0x7ddc3fff0612e75ea5ddc0d6bd4e268f70362cff' AS address, 'harvest_new' AS label),  -- USDT-WETH
    -- Harvest Finance (SushiSwap SLP)
    STRUCT('0x203e97aa6eb65a1a02d9e80083414058303f241e' AS address, 'harvest_sushi' AS label), -- DAI-WETH
    STRUCT('0x01bd09a1124960d9be04b638b142df9df942b04a' AS address, 'harvest_sushi' AS label), -- USDC-WETH
    STRUCT('0x64035b583c8c694627a199243e863bb33be60745' AS address, 'harvest_sushi' AS label), -- USDT-WETH
    -- Pickle Finance (pJar 0.69 - Uniswap LP)
    STRUCT('0xcffa068f1e44d98d3753966ebd58d4cfe3bb5162' AS address, 'pickle' AS label), -- DAI-WETH
    STRUCT('0x53bf2e62fa20e2b4522f05de3597890ec1b352c6' AS address, 'pickle' AS label), -- USDC-WETH
    STRUCT('0x09fc573c502037b149ba87782acc81cf093ec6ef' AS address, 'pickle' AS label),  -- USDT-WETH
    -- Pickle Finance (pJar 0.99 - SushiSwap SLP)
    STRUCT('0x55282da27a3a02ffe599f6d11314d239dac89135' AS address, 'pickle' AS label), -- DAI-WETH
    STRUCT('0x8c2d16b7f6d3f989eb4878ecf13d695a7d504e43' AS address, 'pickle' AS label), -- USDC-WETH
    STRUCT('0xa7a37ae5cb163a3147de83f15e15d8e5f94d6bce' AS address, 'pickle' AS label), -- USDT-WETH
    STRUCT('0x3261d9408604cc8607b687980d40135afa26ffed' AS address, 'pickle' AS label)  -- YFI-WETH
  ])
),

-- UNI挖矿合约白名单
uni_staking AS (
  SELECT address, label FROM UNNEST([
    STRUCT('0x7fba4b8dc5e7616e59622806932dbea72537a56b' AS address, 'uni_mining' AS label), -- USDC-WETH
    STRUCT('0x6c3e4cb2e96b01f4b866965a91ed4437839a121a' AS address, 'uni_mining' AS label), -- USDT-WETH
    STRUCT('0xa1484c3aa22a66c62b77e0ae78e15258bd0cb711' AS address, 'uni_mining' AS label)  -- DAI-WETH
  ])
),

-- 用户列表 

-- 从users_v5.1 获取用户信息
user_info AS (
  SELECT
    user_address,
    pool_name,
    cohort,
    faction,
    balance_0909,
    staked_sushi_0909
  FROM `users1-defi.users_v5.1`
),

-- 构建 (user_address, uni_pool_address) 映射
user_uni_pairs AS (
  SELECT
    ui.user_address,
    up.address AS pool_address,
    ui.pool_name,
    ui.cohort,
    ui.faction,
    ui.balance_0909,
    ui.staked_sushi_0909,
    'uniswap' AS platform
  FROM user_info ui
  JOIN uni_pools up ON ui.pool_name = up.name
),

-- 构建 (user_address, sushi_pool_address) 映射
-- SushiSwap 轨道追踪全部 6 个池
user_sushi_pairs AS (
  SELECT
    ui.user_address,
    sp.address AS pool_address,
    ui.pool_name,
    ui.cohort,
    ui.faction,
    ui.balance_0909,
    ui.staked_sushi_0909,
    'sushiswap' AS platform
  FROM user_info ui
  JOIN sushi_pools sp ON ui.pool_name = sp.name
),

-- 去重的用户地址列表（用于过滤 token_transfers）
distinct_users AS (
  SELECT DISTINCT user_address FROM user_info
),

-- 原始事件获取

raw_events AS (
  SELECT
    t.token_address,
    t.from_address,
    t.to_address,
    CAST(t.value AS BIGNUMERIC) / 1e18 AS val,
    t.block_timestamp,
    t.block_number
  FROM `bigquery-public-data.crypto_ethereum.token_transfers` t
  JOIN all_tracked_tokens att ON t.token_address = att.address
  WHERE t.block_timestamp >= TIMESTAMP('2020-09-09 00:00:00')
    AND t.block_timestamp <  TIMESTAMP('2021-05-05 00:00:00') -- 包含整个5月4日
    AND t.block_number > att.migration_block -- 生存窗口从该池子完成迁移的下一个区块开始
    AND (
      t.from_address IN (SELECT user_address FROM distinct_users)
      OR t.to_address IN (SELECT user_address FROM distinct_users)
    )
),

-- 用户维度事件构造

user_events AS (
  -- 用户接收 token（+）
  SELECT
    to_address     AS user_address,
    token_address  AS pool_address,
    CAST(val AS FLOAT64) AS amount,
    block_timestamp AS event_time,
    block_number,
    from_address   AS counterparty
  FROM raw_events
  WHERE to_address IN (SELECT user_address FROM distinct_users)

  UNION ALL

  -- 用户转出 token（-）
  SELECT
    from_address   AS user_address,
    token_address  AS pool_address,
    -CAST(val AS FLOAT64) AS amount,
    block_timestamp AS event_time,
    block_number,
    to_address     AS counterparty
  FROM raw_events
  WHERE from_address IN (SELECT user_address FROM distinct_users)
),

--分类与运行余额

-- 轨道A: Uniswap LP 事件（匹配 user_uni_pairs）
uni_events_classified AS (
  SELECT
    ue.user_address,
    uup.pool_name,
    'uniswap'       AS platform,
    uup.cohort,
    uup.faction,
    ue.event_time,
    ue.block_number,
    ue.amount,
    ue.counterparty,

    -- 分类 counterparty
    CASE
      WHEN ue.counterparty = '0x0000000000000000000000000000000000000000'
        THEN CASE WHEN ue.amount > 0 THEN 'mint' ELSE 'burn' END
      WHEN ue.counterparty = (SELECT address FROM masterchef)
        THEN 'sushiswap_mc'
      WHEN us.label IS NOT NULL
        THEN us.label
      WHEN ya.label IS NOT NULL
        THEN ya.label
      WHEN ue.counterparty IN (SELECT address FROM uni_pools)
        THEN 'pool_contract'
      ELSE 'user_transfer'
    END AS counterparty_type,

    -- 运行余额：从 balance_0909 开始累加
    -- 忽略向 uni_mining 发送的金额（因为在挖矿合约中的流动性依然属于用户）
    uup.balance_0909 + SUM(
      CASE WHEN us.label IS NOT NULL THEN 0 ELSE ue.amount END
    ) OVER (
      PARTITION BY ue.user_address, uup.pool_name
      ORDER BY ue.event_time, ue.block_number
      ROWS UNBOUNDED PRECEDING
    ) AS running_balance

  FROM user_events ue
  JOIN user_uni_pairs uup
    ON ue.user_address = uup.user_address
   AND ue.pool_address = uup.pool_address
  LEFT JOIN yield_aggregators ya
    ON ue.counterparty = ya.address
  LEFT JOIN uni_staking us
    ON ue.counterparty = us.address
),

-- 轨道B: SushiSwap SLP 事件（匹配 user_sushi_pairs）
sushi_events_classified AS (
  SELECT
    ue.user_address,
    usp.pool_name,
    'sushiswap'     AS platform,
    usp.cohort,
    usp.faction,
    ue.event_time,
    ue.block_number,
    ue.amount,
    ue.counterparty,

    -- 分类 counterparty
    CASE
      WHEN ue.counterparty = '0x0000000000000000000000000000000000000000'
        THEN CASE WHEN ue.amount > 0 THEN 'mint' ELSE 'burn' END
      WHEN ue.counterparty = (SELECT address FROM masterchef)
        THEN 'sushiswap_mc'
      WHEN ya.label IS NOT NULL
        THEN ya.label
      WHEN ue.counterparty IN (SELECT address FROM sushi_pools)
        THEN 'pool_contract'
      ELSE 'user_transfer'
    END AS counterparty_type,

    -- 运行余额：追踪总流动性（Wallet + MasterChef）
    -- 从 0909 时刻质押在 MasterChef 的量开始累加
    -- 忽略所有与 MasterChef 的交互，因为这只是资金在 Wallet 和 MC 之间转移，总流动性不变
    usp.staked_sushi_0909 + SUM(
      CASE WHEN ue.counterparty = (SELECT address FROM masterchef) THEN 0 ELSE ue.amount END
    ) OVER (
      PARTITION BY ue.user_address, usp.pool_name
      ORDER BY ue.event_time, ue.block_number
      ROWS UNBOUNDED PRECEDING
    ) AS running_balance

  FROM user_events ue
  JOIN user_sushi_pairs usp
    ON ue.user_address = usp.user_address
   AND ue.pool_address = usp.pool_address
  LEFT JOIN yield_aggregators ya
    ON ue.counterparty = ya.address
)

--输出 

SELECT * FROM uni_events_classified
UNION ALL
SELECT * FROM sushi_events_classified
ORDER BY user_address, pool_name, platform, event_time, block_number;
