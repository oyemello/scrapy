# John Doe Company

1. [Spent Planning Checklist](#child-163952)
2. [Software Architecture Review](#child-65735)
3. [Analysis 1](#child-131318)
4. [Analysis 2](#child-2523138)


<details id="child-163952">
<summary>Spent Planning Checklist</summary>

 

---

## 1.0) Sprint planning checklist



| **Preparation** | **Meeting** | **Follow up** |
| --- | --- | --- |
| * Review pending finance features * Check quarter-end deadlines * Gather velocity data | * Define sprint goal * Estimate story points * Assign owners | * Update sprint board * Share sprint notes with CFO * Update compliance documentation |

## 2.0) Sprint team members



| **Name** | **Role** |
| --- | --- |
| Sarah Kim | Product Owner (Finance) |
| John Rivera | Scrum Master |
| Priya Mehta | Backend Engineer |
| Tom Nguyen | Frontend Engineer |
| Emily Carter | QA / Compliance Reviewer |

## 3.0) Sprint planning meeting items

Use this template to structure your meeting, set expectations and goals, and define the backlog for the upcoming sprint. For detailed instructions and best practices, see our [sprint planning guide](https://www.atlassian.com/agile/scrum/sprint-planning) and review how to [estimate story points](https://www.atlassian.com/agile/project-management/estimation).

### 3.1) Agenda

1. Review previous sprint deliverables (reports module)
2. Prioritize transaction reconciliation feature
3. Estimate effort for compliance automation tasks
4. Assign owners and set sprint goal

### 3.2) Previous sprint summary



| **Sprint theme** | Automated reporting |
| --- | --- |
| **Story points** | 38 |
| **Summary** | Completed quarterly report export and dashboard filters |

### 3.3) Details



| **Start date** | Sept 12 |
| --- | --- |
| **End date** | Sept 26 |
| **Sprint theme** | Transaction Reconciliation + Compliance Updates |

### 3.4) Velocity tracking

* Last sprint velocity: **38 points**
* Average velocity: **36 points**

### 3.5) Adjusted velocity tracking

* Adjustment: 1 member on PTO for 3 days
* Expected adjusted velocity: **33 points**

### 3.6) Capacity planning

 You can customize this template to change or add capacity measurements. You can also review older sprints by adding columns.



|  | **Current sprint** | **Previous sprint** |
| --- | --- | --- |
| **Total days** | 10 | 10 |
| **Team capacity** | 50 days | 52 days |
| **Projected capacity** | 47 days | 50 days |
| **Individual capacity** | ~9–10 days each | ~10–11 days each |

### 3.7) Potential risks



| **Risk** | **Mitigation** |
| --- | --- |
| Delay in connecting to external bank APIs | Use mock data for development, confirm API access early |
| Compliance rule changes mid-sprint | Schedule weekly sync with compliance officer |
| Data accuracy issues in reconciliation | Add automated test cases with sample ledgers |

## 4.0) Sprint planning resources

### 4.1) Sprint boards and retrospectives

* Jira Finance Dashboard Board (dummy link)
* Sprint 12 retrospective notes

### 4.2) Team resources and definitions

* Definition of Done for Finance Features
* Financial Data Handling Guidelines
* Compliance Checklist


</details>


<details id="child-65735">
<summary>Software Architecture Review</summary>



| **Architecture review date** | Sept 11, 2025 |
| --- | --- |
| **Project lead** | Sarah Kim (Finance Systems Architect) |

 ## Overview

This review covers the financial transaction system that supports reporting, reconciliation, and compliance with quarterly audits. The focus is on scalability, security, and integration with external banking APIs.

## Architecture issues



| **Architecture issue** | **Business impact** | **Priority** | **Notes** |
| --- | --- | --- | --- |
| Slow report generation under load | Delays quarter-end financial close, impacts regulatory deadlines | HIGH | Current queries not optimized for large datasets |
| API throttling with external banks | Failed transaction sync, customer dissatisfaction | HIGH | Need retry/queuing mechanism |
| Legacy authentication system | Security vulnerabilities, audit compliance risks | MEDIUM | Migrate to OAuth 2.0 |
| Inconsistent error logging | Hard to trace reconciliation errors, slows audits | LOW | Standardize logging format |

## Stakeholders



| **Name** | **Role** |
| --- | --- |
| Sarah Kim | Finance Systems Architect |
| John Rivera | CTO |
| Priya Mehta | Backend Engineer |
| Emily Carter | Compliance & Risk Officer |
| CFO’s Office | Business stakeholder |

## Software quality attributes



|  | **Definition** | **Key success metrics** | **Notes** |
| --- | --- | --- | --- |
| **Availability** | System uptime for financial services | 99.95% uptime, < 2 hours downtime per quarter | Must meet SOX compliance |
| Security | Protection of sensitive financial data | No critical vulnerabilities, passed audits | Use encryption at rest + in transit |
| Scalability | Ability to handle peak loads (quarter-end) | Handle 5x normal traffic without degradation | Stress test before Q4 close |
| Performance | Transaction & report latency | < 2s for transactions, < 5s for reports | Optimize queries + caching |
| Auditability | Ability to track & verify transactions | 100% traceability, standardized logs | Key for compliance audits |

## Goals

* Improve performance of quarterly financial reports by 40%
* Enhance security posture with modern authentication and encryption
* Increase system scalability to handle 5x traffic during peak reporting cycles
* Standardize error logging and monitoring for audit readiness

## Next steps



|  | **Project** | **Description** | **Estimate** | **Documentation** | **Target release date** |
| --- | --- | --- | --- | --- | --- |
| 1 | Reporting Optimization | Refactor SQL queries and add caching layer | 3 Sprints | Technical design doc | Nov 2025 |
| 2 | API Resilience | Add retry/queuing for external bank API integration | 2 Sprints | Integration spec | Oct 2025 |
| 3 | Authentication Upgrade | Migrate from legacy auth to OAuth 2.0 + MFA | 4 Sprints | Security migration guide | Dec 2025 |
| 4 | Logging Standardization | Implement structured logs and central monitoring system | 3 Sprints | Logging & monitoring checklist | Oct 2025 |


</details>


<details id="child-131318">
<summary>Analysis 1</summary>

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


</details>


<details id="child-2523138">
<summary>Analysis 2</summary>

## Overview

Discover and resolve problems with your team by following the instructions for the [5 Whys Analysis Play](https://www.atlassian.com/team-playbook/plays/5-whys).



| **Team** | Finance IT Operations |
| --- | --- |
| **Team members** | Alex Johnson, Priya Mehta, Sarah Kim, Tom Nguyen |
| **Date** | Sept 11, 2025 |

 ![image-20250911-183030.png](assets/2523138/image-20250911-183030.png)## Problem statements



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


</details>


<details id="child-2523155">
<summary>Jane Doe Company</summary>

1. [Jane Doe Spent Planning Checklist](jane-doe-company/jane-doe-spent-planning-checklist-2523180.md)
2. [Jane Doe Software Architecture Review](jane-doe-company/jane-doe-software-architecture-review-2523202.md)
3. [Jane Doe Analysis 1](jane-doe-company/jane-doe-analysis-1-2523220.md)
4. <https://mellodoes.atlassian.net/wiki/spaces/efbeee7fd13b425aaf351b3a5c823cdc/pages/393381>


</details>


<details id="child-4554762">
<summary>Josh Doe Company</summary>

[Scrapy Level 1](josh-doe-company/scrapy-level-1-4751361.md) 


</details>

