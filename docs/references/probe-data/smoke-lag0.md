# Block ingest latency probe

- rpc: keyed (rpc-tob.mantle.xyz)
- head_lag_blocks: 0
- block_poll_interval_s: 0.25
- duration_s: 90.6
- warmup_blocks: 5
- total samples: 46
- total gaps: 3 (block-not-found=3)

### all (samples=46)
- latency_ms: n=46 p50=7151 p95=8383 p99=9245 max=9245 mean=7258
- poll_wait_ms: n=46 p50=5967 p95=7050 p99=7730 max=7730 mean=5946
- rpc_work_ms: n=46 p50=1162 p95=1924 p99=2281 max=2281 mean=1311
- block-not-found gaps: 3
- other gaps: 0

### startup (samples=5)
- latency_ms: n=5 p50=8247 p95=9245 p99=9245 max=9245 mean=8259
- poll_wait_ms: n=5 p50=7050 p95=7730 p99=7730 max=7730 mean=6995
- rpc_work_ms: n=5 p50=1197 p95=1710 p99=1710 max=1710 mean=1264
- block-not-found gaps: 0
- other gaps: 0

### steady (samples=41)
- latency_ms: n=41 p50=7130 p95=8063 p99=8383 max=8383 mean=7136
- poll_wait_ms: n=41 p50=5929 p95=6472 p99=6818 max=6818 mean=5819
- rpc_work_ms: n=41 p50=1157 p95=1924 p99=2281 max=2281 mean=1317
- block-not-found gaps: 3
- other gaps: 0
