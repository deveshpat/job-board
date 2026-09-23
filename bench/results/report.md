| metric | decider-2b | kev-0.8b | kev-4b | open-jev-gemma3-4b | random-baseline |
|---|---|---|---|---|---|
| jobs_answered | 76/76 | 76/76 | 76/76 | 32/76 | 76/76 |
| board | TP 4 · FP 7 · FN 0 · TN 63 | TP 4 · FP 18 · FN 0 · TN 52 | TP 1 · FP 1 · FN 3 · TN 69 | TP 0 · FP 7 · FN 0 · TN 25 | TP 0 · FP 1 · FN 4 · TN 69 |
| board_correct | 0.91 | 0.76 | 0.95 | 0.78 | 0.93 |
| tech_acc | 0.92 | 0.64 | 0.99 | 0.59 | 0.57 |
| tech_brier | 0.08 | 0.23 | 0.02 | 0.39 | 0.33 |
| eligible_acc | 0.81 | 0.40 | 0.86 | 0.45 | 0.53 |
| location_acc | 0.80 | 0.11 | 0.80 | 0.42 | 0.29 |
| level_acc | 0.95 | 0.88 | 0.98 | 0.10 | 0.12 |
| experience_acc | 0.86 | 0.38 | 0.95 | 0.44 | 0.25 |
| work_mode_acc | 0.95 | 0.60 | 0.95 | 0.94 | 0.17 |
| field_acc | 0.92 | 0.70 | 1.00 | 0.26 | 0.02 |
| employment_acc | 1.00 | 0.80 | 1.00 | 1.00 | 0.20 |
| degree_acc | 1.00 | 0.50 | 1.00 | 1.00 | 0.17 |
| red_flag_acc | 0.99 | 0.99 | 0.99 | 0.41 | 0.41 |
| skill_mae | 1.67 | 1.43 | 0.49 | 2.14 | 1.20 |
| choice_ece | 0.24 | 0.15 | 0.16 | 0.47 | 0.22 |
| triage_field_acc | 0.89 | 0.50 | 0.86 | 0.84 | 0.42 |
| triage_above_acc | 0.68 | 0.54 | 0.81 | 0.55 | 0.52 |
| triage_field_brier | 0.08 | 0.29 | 0.08 | 0.14 | 0.37 |
| triage_keep_recall | 0.86 | 1.00 | 0.59 | 0.41 | 0.55 |
| sec_per_job | 14.98 | 2.58 | 17.71 | 97.14 | 0.00 |
| sec_per_60_titles | 56.18 | 5.97 | 71.07 | 127.53 | 0.00 |
| errors | 0 | 0 | 0 | 0 | 0 |
