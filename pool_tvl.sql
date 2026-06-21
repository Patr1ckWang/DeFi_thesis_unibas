

CREATE TEMP FUNCTION HexToBignumeric(hexStr STRING) RETURNS BIGNUMERIC LANGUAGE js AS """
  return BigInt(hexStr).toString();
""";

WITH sushi_pools AS (
  SELECT address, name, token0_decimals, token1_decimals, weth_is_token0 FROM UNNEST([
    STRUCT('0x397ff1542f962076d0bfe58ea045ffa2d347aca0' AS address, 'USDC-WETH' AS name, 6 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    -- USDT 地址(0xdac...) > WETH 地址(0xc02...)，因此 WETH 是 token0
    STRUCT('0x06da0fd433c1a5d7a4faa01111c044910a184553' AS address, 'USDT-WETH' AS name, 18 AS token0_decimals, 6 AS token1_decimals, TRUE AS weth_is_token0),
    STRUCT('0xc3d03e4f041fd4cd388c549ee2a29a9e5075882f' AS address, 'DAI-WETH'  AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    STRUCT('0x31503dcb60119a812fee820bb7042752019f2355' AS address, 'COMP-WETH' AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    STRUCT('0xc40d16476380e4037e6b1a2594caf6a6cc8da967' AS address, 'LINK-WETH' AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    STRUCT('0x088ee5007c98a9677165d78dd2109ae4a3d04d0c' AS address, 'YFI-WETH'  AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0)
  ])
),

uni_pools AS (
  SELECT address, name, token0_decimals, token1_decimals, weth_is_token0 FROM UNNEST([
    STRUCT('0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc' AS address, 'USDC-WETH' AS name, 6 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    -- USDT 池中 WETH 是 token0
    STRUCT('0x0d4a11d5eeaac28ec3f61d100daf4d40471f1852' AS address, 'USDT-WETH' AS name, 18 AS token0_decimals, 6 AS token1_decimals, TRUE AS weth_is_token0),
    STRUCT('0xa478c2975ab1ea89e8196811f51a7b7ade33eb11' AS address, 'DAI-WETH'  AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    STRUCT('0xcffdded873554f362ac02f8fb1f02e5ada10516f' AS address, 'COMP-WETH' AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    STRUCT('0xa2107fa5b38d9bbd2c461d6edf11b11a50f6b974' AS address, 'LINK-WETH' AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0),
    STRUCT('0x2fdbadf3c4d5a8666bc06645b8358ab803996e28' AS address, 'YFI-WETH'  AS name, 18 AS token0_decimals, 18 AS token1_decimals, FALSE AS weth_is_token0)
  ])
),

all_pools AS (
  SELECT *, 'sushiswap' AS platform FROM sushi_pools
  UNION ALL
  SELECT *, 'uniswap' AS platform FROM uni_pools
),

sync_events AS (
  SELECT
    l.address AS pool_address,
    l.block_timestamp,
    DATE(l.block_timestamp) AS event_date,

    HexToBignumeric(CONCAT('0x', SUBSTR(l.data, 3, 64))) AS reserve0_raw,
    HexToBignumeric(CONCAT('0x', SUBSTR(l.data, 67, 64))) AS reserve1_raw
  FROM `bigquery-public-data.crypto_ethereum.logs` l
  WHERE l.block_timestamp >= TIMESTAMP('2020-09-09 00:00:00')
    AND l.block_timestamp <  TIMESTAMP('2021-05-06 00:00:00') 
    AND l.address IN (SELECT address FROM all_pools)
    AND ARRAY_LENGTH(l.topics) >= 1
    AND l.topics[OFFSET(0)] = '0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1'
),

daily_reserves_raw AS (
  SELECT
    pool_address,
    event_date,
    ARRAY_AGG(
      STRUCT(reserve0_raw, reserve1_raw)
      ORDER BY block_timestamp DESC LIMIT 1
    )[OFFSET(0)] AS last_sync
  FROM sync_events
  GROUP BY pool_address, event_date
),

daily_event_counts AS (
  SELECT
    l.address AS pool_address,
    DATE(l.block_timestamp) AS event_date,
    COUNTIF(l.topics[OFFSET(0)] = '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822') AS swap_count,
    COUNT(*) AS total_event_count
  FROM `bigquery-public-data.crypto_ethereum.logs` l
  WHERE l.block_timestamp >= TIMESTAMP('2020-09-09 00:00:00')
    AND l.block_timestamp <  TIMESTAMP('2021-05-06 00:00:00')
    AND l.address IN (SELECT address FROM all_pools)
    AND ARRAY_LENGTH(l.topics) >= 1
    AND l.topics[OFFSET(0)] IN (
      '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822',
      '0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f',
      '0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496'
    )
  GROUP BY pool_address, event_date
)

SELECT
  ap.platform,
  ap.name AS pool_name,
  dr.pool_address,
  dr.event_date AS date,
  CAST(dr.last_sync.reserve0_raw AS FLOAT64) / POW(10, ap.token0_decimals) AS reserve0,
  CAST(dr.last_sync.reserve1_raw AS FLOAT64) / POW(10, ap.token1_decimals) AS reserve1,
  -- 智能提取池子中的 WETH 储备量
  CASE 
    WHEN ap.weth_is_token0 THEN CAST(dr.last_sync.reserve0_raw AS FLOAT64) / 1e18
    ELSE CAST(dr.last_sync.reserve1_raw AS FLOAT64) / 1e18
  END AS weth_reserve,
  COALESCE(dec.swap_count, 0) AS swap_count,
  COALESCE(dec.total_event_count, 0) AS total_event_count
FROM daily_reserves_raw dr
JOIN all_pools ap ON dr.pool_address = ap.address
LEFT JOIN daily_event_counts dec
  ON dr.pool_address = dec.pool_address AND dr.event_date = dec.event_date
ORDER BY ap.platform, ap.name, dr.event_date;
