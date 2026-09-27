# Evaluation Report

Generated 2026-09-27T14:21:18.795873Z by `scripts/run_benchmark.py` against 30 benchmark cases (`data/benchmark_incidents.json`). Methodology: [EVALUATION.md](EVALUATION.md).

## Aggregate Metrics

| # | Metric | Value |
|---|---|---|
| 1 | Root-cause identification accuracy | 57% |
| 2 | First-action accuracy | 80% |
| 3 | Successful autonomous resolution rate | 13% |
| 4 | Verification success rate | 63% |
| 5 | Escalation appropriateness | 90% |
| 6 | Mean diagnostic steps | 4.17 |
| 7 | Mean time to resolution (simulated minutes) | 33.10 |
| 8 | Human intervention rate | 87% |
| 9 | False remediation rate | 7% |
| 10 | Tool selection accuracy | 40% |
| 11 | Evidence utilization | 100% |
| 12 | SLA breach rate | 0% |

## Per-Case Results

| Benchmark ID | Incident | Ground Truth | Final Stage | Root Cause OK | First Action OK | Escalation OK | Diag Steps | Tool Sel. Acc. | Evidence Util. | SLA Breach |
|---|---|---|---|---|---|---|---|---|---|---|
| BENCH-SCN-001 | EVAL-001 | connectivity_failure | RESOLVED | True | False | True | 4 | 25% | 100% | False |
| BENCH-SCN-002 | EVAL-002 | auth_failure | RESOLVED | True | True | True | 4 | 50% | 100% | False |
| BENCH-SCN-003 | EVAL-003 | database_connection_failure | ESCALATED | False | True | True | 4 | 50% | 100% | False |
| BENCH-SCN-004 | EVAL-004 | connectivity_failure | ESCALATED | False | False | True | 4 | 25% | 100% | False |
| BENCH-SCN-005 | EVAL-005 | auth_failure | ESCALATED | False | True | True | 4 | 50% | 100% | False |
| BENCH-SCN-006 | EVAL-006 | n/a | ESCALATED | None | True | True | 4 | 100% | 100% | False |
| BENCH-SCN-007 | EVAL-007 | mapping_error | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-SCN-008 | EVAL-008 | duplicate_message | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-SCN-009 | EVAL-009 | n/a | ESCALATED | None | True | True | 4 | 100% | 100% | False |
| BENCH-SCN-010 | EVAL-010 | mapping_error | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0041 | INC-0041 | auth_failure | RESOLVED | True | False | True | 4 | 50% | 100% | False |
| BENCH-INC-0049 | INC-0049 | cert_expiry | RESOLVED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0058 | INC-0058 | connectivity_failure | RESOLVED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0012 | INC-0012 | database_connection_failure | RESOLVED | False | True | True | 4 | 50% | 100% | False |
| BENCH-INC-0003 | INC-0003 | duplicate_message | ESCALATED | True | True | False | 6 | 50% | 100% | False |
| BENCH-INC-0013 | INC-0013 | endpoint_unavailable | RESOLVED | False | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0091 | INC-0091 | malformed_payload | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0072 | INC-0072 | mapping_error | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0067 | INC-0067 | message_processing_failure | RESOLVED | True | True | True | 5 | 40% | 100% | False |
| BENCH-INC-0010 | INC-0010 | sftp_credential_failure | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0064 | INC-0064 | timeout | RESOLVED | True | True | True | 4 | 50% | 100% | False |
| BENCH-INC-0092 | INC-0092 | database_connection_failure | ESCALATED | False | False | False | 4 | 50% | 100% | False |
| BENCH-INC-0037 | INC-0037 | database_connection_failure | RESOLVED | False | True | True | 4 | 50% | 100% | False |
| BENCH-INC-0025 | INC-0025 | endpoint_unavailable | ESCALATED | False | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0063 | INC-0063 | malformed_payload | ESCALATED | True | True | True | 4 | 25% | 100% | False |
| BENCH-INC-0085 | INC-0085 | duplicate_message | ESCALATED | False | False | False | 4 | 25% | 100% | False |
| BENCH-INC-0054 | INC-0054 | auth_failure | RESOLVED | True | True | True | 4 | 50% | 100% | False |
| BENCH-INC-0047 | INC-0047 | auth_failure | ESCALATED | False | True | True | 5 | 60% | 100% | False |
| BENCH-INC-0096 | INC-0096 | endpoint_unavailable | RESOLVED | False | False | True | 4 | 25% | 100% | False |
| BENCH-INC-0016 | INC-0016 | cert_expiry | ESCALATED | False | True | True | 5 | 40% | 100% | False |

## Notable Failures

- **BENCH-SCN-003** (EVAL-003): wrong root cause identified. Final stage: ESCALATED, ground truth: database_connection_failure.
- **BENCH-SCN-004** (EVAL-004): wrong root cause identified. Final stage: ESCALATED, ground truth: connectivity_failure.
- **BENCH-SCN-005** (EVAL-005): wrong root cause identified. Final stage: ESCALATED, ground truth: auth_failure.
- **BENCH-INC-0012** (INC-0012): wrong root cause identified. Final stage: RESOLVED, ground truth: database_connection_failure.
- **BENCH-INC-0003** (INC-0003): escalation decision did not match expectation. Final stage: ESCALATED, ground truth: duplicate_message.
- **BENCH-INC-0013** (INC-0013): wrong root cause identified. Final stage: RESOLVED, ground truth: endpoint_unavailable.
- **BENCH-INC-0092** (INC-0092): wrong root cause identified, escalation decision did not match expectation. Final stage: ESCALATED, ground truth: database_connection_failure.
- **BENCH-INC-0037** (INC-0037): wrong root cause identified. Final stage: RESOLVED, ground truth: database_connection_failure.
- **BENCH-INC-0025** (INC-0025): wrong root cause identified. Final stage: ESCALATED, ground truth: endpoint_unavailable.
- **BENCH-INC-0063** (INC-0063): executed a remediation outside the acceptable set. Final stage: ESCALATED, ground truth: malformed_payload.
- **BENCH-INC-0085** (INC-0085): wrong root cause identified, escalation decision did not match expectation, executed a remediation outside the acceptable set. Final stage: ESCALATED, ground truth: duplicate_message.
- **BENCH-INC-0047** (INC-0047): wrong root cause identified. Final stage: ESCALATED, ground truth: auth_failure.
- **BENCH-INC-0096** (INC-0096): wrong root cause identified. Final stage: RESOLVED, ground truth: endpoint_unavailable.
- **BENCH-INC-0016** (INC-0016): wrong root cause identified. Final stage: ESCALATED, ground truth: cert_expiry.
