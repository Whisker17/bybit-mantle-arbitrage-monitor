# Block ingest latency probe

- rpc: public (rpc.mantle.xyz)
- head_lag_blocks: 1
- block_poll_interval_s: 0.25
- duration_s: 600.4
- warmup_blocks: 30
- total samples: 299
- total gaps: 0 (block-not-found=0)

### all (samples=299)
- latency_ms: n=299 p50=12253 p95=28494 p99=30194 max=30689 mean=14090
- poll_wait_ms: n=299 p50=10595 p95=26494 p99=28204 max=28689 mean=12457
- rpc_work_ms: n=299 p50=1459 p95=2815 p99=4026 max=6715 mean=1632
- block-not-found gaps: 0
- other gaps: 0

### startup (samples=30)
- latency_ms: n=30 p50=11004 p95=11897 p99=12253 max=12253 mean=11028
- poll_wait_ms: n=30 p50=9488 p95=10464 p99=10628 max=10628 mean=9603
- rpc_work_ms: n=30 p50=1348 p95=1995 p99=2104 max=2104 mean=1425
- block-not-found gaps: 0
- other gaps: 0

### steady (samples=269)
- latency_ms: n=269 p50=12436 p95=28577 p99=30194 max=30689 mean=14431
- poll_wait_ms: n=269 p50=10812 p95=26577 p99=28204 max=28689 mean=12776
- rpc_work_ms: n=269 p50=1472 p95=2817 p99=4026 max=6715 mean=1656
- block-not-found gaps: 0
- other gaps: 0
