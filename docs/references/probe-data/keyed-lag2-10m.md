# Block ingest latency probe

- rpc: keyed (rpc-tob.mantle.xyz)
- head_lag_blocks: 2
- block_poll_interval_s: 0.25
- duration_s: 600.5
- warmup_blocks: 30
- total samples: 302
- total gaps: 0 (block-not-found=0)

### all (samples=302)
- latency_ms: n=302 p50=10739 p95=13248 p99=14040 max=14531 mean=10921
- poll_wait_ms: n=302 p50=9340 p95=11645 p99=12447 max=12688 mean=9474
- rpc_work_ms: n=302 p50=1351 p95=2248 p99=2939 max=4229 mean=1447
- block-not-found gaps: 0
- other gaps: 0

### startup (samples=30)
- latency_ms: n=30 p50=11197 p95=12563 p99=12735 max=12735 mean=11166
- poll_wait_ms: n=30 p50=9704 p95=11338 p99=11623 max=11623 mean=9741
- rpc_work_ms: n=30 p50=1394 p95=2103 p99=2606 max=2606 mean=1425
- block-not-found gaps: 0
- other gaps: 0

### steady (samples=272)
- latency_ms: n=272 p50=10700 p95=13411 p99=14183 max=14531 mean=10894
- poll_wait_ms: n=272 p50=9293 p95=11724 p99=12531 max=12688 mean=9444
- rpc_work_ms: n=272 p50=1350 p95=2270 p99=2940 max=4229 mean=1450
- block-not-found gaps: 0
- other gaps: 0
