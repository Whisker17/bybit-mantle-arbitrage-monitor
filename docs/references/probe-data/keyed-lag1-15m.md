# Block ingest latency probe

- rpc: keyed (rpc-tob.mantle.xyz)
- head_lag_blocks: 1
- block_poll_interval_s: 0.25
- duration_s: 900.5
- warmup_blocks: 30
- total samples: 451
- total gaps: 0 (block-not-found=0)

### all (samples=451)
- latency_ms: n=451 p50=8838 p95=11383 p99=12303 max=13069 mean=9036
- poll_wait_ms: n=451 p50=7430 p95=9701 p99=10529 max=11312 mean=7562
- rpc_work_ms: n=451 p50=1420 p95=2164 p99=2570 max=3059 mean=1474
- block-not-found gaps: 0
- other gaps: 0

### startup (samples=30)
- latency_ms: n=30 p50=8866 p95=9819 p99=9880 max=9880 mean=8940
- poll_wait_ms: n=30 p50=7509 p95=8301 p99=8660 max=8660 mean=7558
- rpc_work_ms: n=30 p50=1377 p95=1850 p99=2040 max=2040 mean=1382
- block-not-found gaps: 0
- other gaps: 0

### steady (samples=421)
- latency_ms: n=421 p50=8823 p95=11398 p99=12303 max=13069 mean=9043
- poll_wait_ms: n=421 p50=7402 p95=9732 p99=10529 max=11312 mean=7562
- rpc_work_ms: n=421 p50=1427 p95=2173 p99=2570 max=3059 mean=1481
- block-not-found gaps: 0
- other gaps: 0
