# 5.4 Jane Doe Analysis 2

## Overview

Discover and resolve problems with your team by following the instructions for the [5 Whys Analysis Play](https://www.atlassian.com/team-playbook/plays/5-whys).



| **Team** | Finance IT Operations |
| --- | --- |
| **Team members** | Alex Johnson, Priya Mehta, Sarah Kim, Tom Nguyen |
| **Date** | Sept 11, 2025 |

![image-20250911-183030.png](../assets/2523247/image-20250911-183030.png)## Problem statements



| **Problem 1** | **Problem 2** | **Problem 3** | **Problem 4** | **Problem 5** |
| --- | --- | --- | --- | --- |
| Delayed customer refunds | Inaccurate financial forecasts | Slow credit card transaction processing | High number of compliance violations | High operational costs in finance IT |
| **Whys** | **Whys** | **Whys** | **Whys** | **Whys** |
| * Refund requests sit in pending status too long * Manual approval needed for all cases * No automated rules to classify low-risk refunds * Legacy workflow tool doesn’t support automation * No budget allocated for upgrading refund system | * Forecasts deviate heavily from actuals * Data sources are inconsistent * Different teams use different spreadsheet templates * No centralized forecasting tool * Lack of governance on financial planning processes | * Customers experience long wait times for payments to post * Batch processing runs only twice a day * System not designed for real-time updates * Core payment engine outdated and hard to scale * Upgrades deprioritized due to budget constraints | * Reports often miss required fields * Teams manually prepare compliance submissions * No validation checks before sending to regulators * Compliance rules are complex and frequently updated * No automated monitoring or rule engine in place | * Monthly cloud bills are increasing * Multiple unused instances kept running * No cost monitoring dashboards * Responsibility for cleanup not clearly assigned * Lack of FinOps (financial operations) practices in team |
| **Final problem statement** | | | | |
| Costs are high because there’s no ownership or monitoring framework for managing cloud resources. | | | | |

## Solutions



| **Solution** | **Owner** | **Status** | **Action items** |
| --- | --- | --- | --- |
| Automate low-risk refund approvals | Priya Mehta | IN PROGRESS | Build rules engine for refunds under $1000 |
| Implement centralized forecasting tool | Alex Johnson | NOT STARTED | Evaluate vendor options (Anaplan, Workday) |
| Upgrade payment system to real-time | Tom Nguyen | NOT STARTED | Draft architecture plan for replacing batch engine |
| Add compliance validation automation | Sarah Kim | COMPLETE | Integrated validation checks before submissions |
| Establish FinOps monitoring & ownership | DevOps Team | IN PROGRESS | Create dashboards, assign resource cleanup roles |

