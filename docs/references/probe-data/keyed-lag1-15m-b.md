# Block ingest latency probe

- rpc: keyed (rpc-tob.mantle.xyz)
- head_lag_blocks: 1
- block_poll_interval_s: 0.25
- duration_s: 901.0
- warmup_blocks: 30
- total samples: 450
- total gaps: 30 (block-not-found=30)

### all (samples=450)
- latency_ms: n=450 p50=8150 p95=12594 p99=13541 max=14265 mean=8486
- poll_wait_ms: n=450 p50=6981 p95=10903 p99=11888 max=12266 mean=7388
- rpc_work_ms: n=450 p50=1118 p95=2268 p99=2711 max=3070 mean=1098
- block-not-found gaps: 30
- other gaps: 0

### startup (samples=30)
- latency_ms: n=30 p50=5755 p95=6852 p99=7225 max=7225 mean=5789
- poll_wait_ms: n=30 p50=5361 p95=6540 p99=6906 max=6906 mean=5447
- rpc_work_ms: n=30 p50=329 p95=439 p99=490 max=490 mean=342
- block-not-found gaps: 3
- other gaps: 0

### steady (samples=420)
- latency_ms: n=420 p50=8277 p95=12594 p99=13541 max=14265 mean=8679
- poll_wait_ms: n=420 p50=7099 p95=10917 p99=11888 max=12266 mean=7527
- rpc_work_ms: n=420 p50=1150 p95=2325 p99=2711 max=3070 mean=1152
- block-not-found gaps: 28
- other gaps: 0
