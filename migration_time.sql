SELECT
  token_address AS sushi_pool_address,
  MIN(block_number) AS migration_block,
  MIN(block_timestamp) AS migration_time,
  SUM(CAST(value AS BIGNUMERIC) / 1e18) AS total_migrated_liquidity
FROM `bigquery-public-data.crypto_ethereum.token_transfers`
WHERE to_address = '0xc2edad668740f1aa35e4d8f227fb8e17dca888cd' -- 接收方是 MasterChef
  AND from_address = '0x0000000000000000000000000000000000000000' -- 发送方是 0x0 (Mint)
  AND block_timestamp >= TIMESTAMP('2020-09-09 00:00:00')
  AND block_timestamp < TIMESTAMP('2020-09-10 00:00:00')
  AND token_address IN (
    '0x397ff1542f962076d0bfe58ea045ffa2d347aca0', -- USDC-WETH
    '0x06da0fd433c1a5d7a4faa01111c044910a184553', -- USDT-WETH
    '0xc3d03e4f041fd4cd388c549ee2a29a9e5075882f', -- DAI-WETH
    '0x31503dcb60119a812fee820bb7042752019f2355', -- COMP-WETH
    '0xc40d16476380e4037e6b1a2594caf6a6cc8da967', -- LINK-WETH
    '0x088ee5007c98a9677165d78dd2109ae4a3d04d0c'  -- YFI-WETH
  )
GROUP BY token_address
ORDER BY migration_block ASC;
