# 3 Analysis 1

## Overview

Discover and resolve problems with your team by following the instructions for the [5 Whys Analysis Play](https://www.atlassian.com/team-playbook/plays/5-whys).



| **Team** | Finance Systems Squad |
| --- | --- |
| **Team members** | Sarah Kim, John Rivera, Priya Mehta, Emily Carter |
| **Date** | Sept 11, 2025 |

 ![image-20250911-183050.png](assets/131318/image-20250911-183050.png)## Problem statements



| **Problem 1** | **Problem 2** | **Problem 3** | **Problem 4** | **Problem 5** |
| --- | --- | --- | --- | --- |
| Delayed month-end financial reports | Failed reconciliation of bank transactions | Frequent duplicate invoices | High support ticket volume for payroll issues | Audit trail gaps in system |
| **Whys** | **Whys** | **Whys** | **Whys** | **Whys** |
| * Reports take too long to generate * Database queries are inefficient * Missing indexes on transaction-heavy tables * Schema wasn’t updated after onboarding new clients * No regular database performance review process | * Transactions are mismatched or missing * External bank API occasionally drops requests * No retry or queuing mechanism in place * Integration design assumed stable API availability * Lack of resilience planning during architecture review | * Duplicate invoices appear in the system * Validation checks are missing at submission * Invoice module relies on manual data entry * No deduplication rules or automated checks * Business rules for invoice uniqueness not defined clearly | * Employees frequently report incorrect payroll amounts * Calculation errors in payroll processing * Outdated formulas not updated for new tax rules * No automated tax table update mechanism * Manual dependency on HR team to update tax logic | * Some actions are not logged in the system * Developers didn’t add logging to all critical functions * No central standard for what events must be logged * Logging wasn’t prioritized during initial development * Compliance requirements were not fully captured at the start |
| **Final problem statement** | | | | |
| Month-end reports are delayed because the database was not optimized to handle scaled transaction volumes, and there is no regular performance tuning process. | | | | |

## Solutions



| **Solution** | **Owner** | **Status** | **Action items** |
| --- | --- | --- | --- |
| Optimize queries and add DB indexes | Priya Mehta | IN PROGRESS | Identify slow queries, add indexes, run benchmarks |
| Establish quarterly DB performance review | John Rivera | NOT STARTED | Add review to sprint backlog, assign DBA |
| Improve reconciliation automation | Sarah Kim | COMPLETE | Deployed new script to auto-match 85% of entries |
| Add invoice validation checks | Emily Carter | IN PROGRESS | Implement duplicate detection before posting |
| Strengthen audit logging framework | DevOps Team | NOT STARTED | Standardize log format, route to central monitoring |

