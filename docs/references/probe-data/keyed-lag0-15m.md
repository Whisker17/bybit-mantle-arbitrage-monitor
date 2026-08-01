# Block ingest latency probe

- rpc: keyed (rpc-tob.mantle.xyz)
- head_lag_blocks: 0
- block_poll_interval_s: 0.25
- duration_s: 934.1
- warmup_blocks: 30
- total samples: 414
- total gaps: 13 (block-not-found=9)

### all (samples=414)
- latency_ms: n=414 p50=7333 p95=48799 p99=78965 max=88796 mean=15001
- poll_wait_ms: n=414 p50=5860 p95=44340 p99=76286 max=85788 mean=13211
- rpc_work_ms: n=414 p50=1320 p95=4328 p99=11464 max=13928 mean=1790
- block-not-found gaps: 9
- other gaps: 4

### startup (samples=30)
- latency_ms: n=30 p50=7923 p95=11622 p99=11650 max=11650 mean=8354
- poll_wait_ms: n=30 p50=6184 p95=9622 p99=9651 max=9651 mean=6754
- rpc_work_ms: n=30 p50=1335 p95=2602 p99=4105 max=4105 mean=1600
- block-not-found gaps: 2
- other gaps: 0

### steady (samples=384)
- latency_ms: n=384 p50=7287 p95=49219 p99=81266 max=88796 mean=15520
- poll_wait_ms: n=384 p50=5837 p95=44644 p99=76966 max=85788 mean=13715
- rpc_work_ms: n=384 p50=1317 p95=4389 p99=11949 max=13928 mean=1805
- block-not-found gaps: 7
- other gaps: 4
