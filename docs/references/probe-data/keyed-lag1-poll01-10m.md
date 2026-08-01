# Block ingest latency probe

- rpc: keyed (rpc-tob.mantle.xyz)
- head_lag_blocks: 1
- block_poll_interval_s: 0.1
- duration_s: 600.0
- warmup_blocks: 30
- total samples: 302
- total gaps: 77 (block-not-found=72)

### all (samples=302)
- latency_ms: n=302 p50=5967 p95=7682 p99=8803 max=9680 mean=6019
- poll_wait_ms: n=302 p50=5598 p95=7093 p99=7677 max=7891 mean=5602
- rpc_work_ms: n=302 p50=347 p95=750 p99=1657 max=3498 mean=417
- block-not-found gaps: 72
- other gaps: 5

### startup (samples=30)
- latency_ms: n=30 p50=6628 p95=9289 p99=9680 max=9680 mean=6729
- poll_wait_ms: n=30 p50=5594 p95=7680 p99=7891 max=7891 mean=5822
- rpc_work_ms: n=30 p50=457 p95=1789 p99=3498 max=3498 mean=906
- block-not-found gaps: 5
- other gaps: 0

### steady (samples=272)
- latency_ms: n=272 p50=5957 p95=7348 p99=7757 max=8221 mean=5941
- poll_wait_ms: n=272 p50=5598 p95=7038 p99=7373 max=7837 mean=5578
- rpc_work_ms: n=272 p50=342 p95=516 p99=726 max=1115 mean=364
- block-not-found gaps: 71
- other gaps: 5
