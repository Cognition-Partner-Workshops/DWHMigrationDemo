# MigraAI session conversion report

## 1. Summary

- Package: migraai-teradata-to-snowflake
- Run ID: sample-teradata-snowflake
- Created: 2026-01-01T00:00:00Z
- Total objects: 56
- converted: 56
- expert_review: 0
- failed: 0
- cancelled: 0
- Included in package: 56
- converted: 56
- escalated: 0
- expert review: 0
- failed: 0

## 2. Source and target

- Source platform: teradata
- Target platform: snowflake
- Confidence threshold: 85%
- KB version: n/a

## 3. Scope and inventory

| Object type | Count |
|---|---|
| grant | 7 |
| hash\_index | 2 |
| index | 2 |
| join\_index | 1 |
| macro | 1 |
| procedure | 5 |
| role | 3 |
| script | 1 |
| synonym | 1 |
| table | 21 |
| user | 2 |
| view | 10 |

Source files:

- indexes.sql
- load\_script.sql
- maintenance.sql
- routines.sql
- security.sql
- tables.sql
- views.sql

External references:

- sales.customer\_archive

## 4. Data sent to Devin

- Consent given at: 2026-01-01T00:00:00Z
- Devin host: local mock
- Objects sent: 56
- Batches: 3
- Sessions: 9
- Total ACUs: not reported by the Devin API
- Blocked batches: 0


| Role | Batch | State | ACUs | Link |
|---|---|---|---|---|
| voter\_a | 0 | done | n/a | mock://golden/session-0000 |
| voter\_a | 1 | done | n/a | mock://golden/session-0001 |
| voter\_a | 2 | done | n/a | mock://golden/session-0002 |
| voter\_b | 0 | done | n/a | mock://golden/session-0003 |
| voter\_b | 1 | done | n/a | mock://golden/session-0004 |
| voter\_b | 2 | done | n/a | mock://golden/session-0005 |
| voter\_c | 0 | done | n/a | mock://golden/session-0006 |
| voter\_c | 1 | done | n/a | mock://golden/session-0007 |
| voter\_c | 2 | done | n/a | mock://golden/session-0008 |

## 5. Conversion results

| Name | Type | Status | Confidence % | Agreement % | Escalated |
|---|---|---|---|---|---|
| sales.recovered\_after\_error | table | converted | 98 | 100 | no |
| reader | role | converted | 98 | 100 | no |
| operator | role | converted | 98 | 100 | no |
| auditor | role | converted | 98 | 100 | no |
| analyst | user | converted | 98 | 100 | no |
| loader | user | converted | 98 | 100 | no |
| ROLE on reader | grant | converted | 98 | 100 | no |
| ROLE on operator | grant | converted | 98 | 100 | no |
| sales.customer | table | converted | 98 | 100 | no |
| sales.account | table | converted | 98 | 100 | no |
| sales.branch | table | converted | 98 | 100 | no |
| sales.employee | table | converted | 98 | 100 | no |
| sales.product | table | converted | 98 | 100 | no |
| sales.order\_header | table | converted | 98 | 100 | no |
| sales.order\_line | table | converted | 98 | 100 | no |
| sales.payment | table | converted | 98 | 100 | no |
| sales.loan | table | converted | 98 | 100 | no |
| sales.loan\_payment | table | converted | 98 | 100 | no |
| sales.card | table | converted | 98 | 100 | no |
| sales.card\_txn | table | converted | 98 | 100 | no |
| sales.transfer | table | converted | 98 | 100 | no |
| sales.audit\_log | table | converted | 98 | 100 | no |
| sales.risk\_score | table | converted | 98 | 100 | no |
| sales.account\_snapshot | table | converted | 98 | 100 | no |
| sales.customer\_segment | table | converted | 98 | 100 | no |
| sales.external\_rate | table | converted | 98 | 100 | no |
| sales.daily\_balance | table | converted | 98 | 100 | no |
| sales.recovered\_table | table | converted | 98 | 100 | no |
| sales.ix\_customer\_status | index | converted | 92 | 100 | no |
| sales.ix\_account\_customer | index | converted | 92 | 100 | no |
| sales.hix\_account | hash\_index | converted | 98 | 100 | no |
| sales.hix\_card | hash\_index | converted | 98 | 100 | no |
| sales.jix\_order | join\_index | converted | 98 | 100 | no |
| sales.customer\_alias | synonym | converted | 98 | 100 | no |
| load\_script.sql | script | converted | 92 | 100 | no |
| sales.add\_payment | procedure | converted | 87 | 100 | no |
| sales.update\_balance | procedure | converted | 87 | 100 | no |
| sales.post\_transfer | procedure | converted | 87 | 100 | no |
| sales.capture\_snapshot | procedure | converted | 92 | 100 | no |
| sales.repair\_payment | procedure | converted | 87 | 100 | no |
| sales.refresh\_daily | macro | converted | 92 | 100 | no |
| SELECT, INSERT on sales.payment | grant | converted | 98 | 100 | no |
| SELECT on sales.audit\_log | grant | converted | 98 | 100 | no |
| EXECUTE PROCEDURE on sales.add\_payment | grant | converted | 98 | 100 | no |
| sales.customer\_accounts | view | converted | 98 | 100 | no |
| sales.account\_payments | view | converted | 98 | 100 | no |
| sales.customer\_loans | view | converted | 98 | 100 | no |
| sales.order\_detail | view | converted | 98 | 100 | no |
| sales.card\_activity | view | converted | 98 | 100 | no |
| sales.transfer\_activity | view | converted | 98 | 100 | no |
| sales.customer\_risk | view | converted | 98 | 100 | no |
| sales.daily\_account\_balances | view | converted | 98 | 100 | no |
| sales.customer\_external\_rate | view | converted | 98 | 100 | no |
| sales.unuploaded\_reference | view | converted | 98 | 100 | no |
| SELECT on sales.customer\_accounts | grant | converted | 98 | 100 | no |
| SELECT on sales.account\_payments | grant | converted | 98 | 100 | no |

## 6. Validation

| Check | Passed | Failed | N/A | Pass rate |
|---|---|---|---|---|
| syntax | 50 | 6 | 0 | 89% |
| lint | 56 | 0 | 0 | 100% |
| semantic | 56 | 0 | 0 | 100% |
| rule | 49 | 7 | 0 | 88% |
| dependency | 31 | 0 | 25 | 100% |
| object\_reference | 28 | 0 | 28 | 100% |
| security | 56 | 0 | 0 | 100% |

Top findings:

| Rule | Check | Severity | Count |
|---|---|---|---|
| syntax.opaque\_statement | syntax | warning | 13 |
| syntax.parse\_error | syntax | error | 6 |
| snowflake.rule.procedure\_execute\_as | rule | error | 5 |
| security.masked\_secret\_placeholder | security | warning | 3 |
| snowflake.rule.no\_create\_index | rule | error | 2 |

## 7. Expert review and failed items

None.

## 8. Assumptions, unsupported constructs and masked secrets

### Assumptions

None.

### Unsupported constructs

None.

### Masked secrets

- WARNING: analyst (security-converted.sql) contains the masked placeholder `***MASKED***`. Replace each one with a secret reference for the target (for example a secret store or environment variable) before running.
- WARNING: loader (security-converted.sql) contains the masked placeholder `***MASKED***`. Replace each one with a secret reference for the target (for example a secret store or environment variable) before running.
- WARNING: load\_script.sql (load\_script-converted.sql) contains the masked placeholder `***MASKED***`. Replace each one with a secret reference for the target (for example a secret store or environment variable) before running.

## 9. Execution order and next steps

1. SALES.RECOVERED\_AFTER\_ERROR (table, converted) -> converted/maintenance-converted.sql
2. reader (role, converted) -> converted/security-converted.sql
3. operator (role, converted) -> converted/security-converted.sql
4. auditor (role, converted) -> converted/security-converted.sql
5. analyst (user, converted) -> converted/security-converted.sql
6. loader (user, converted) -> converted/security-converted.sql
7. ROLE on reader (grant, converted) -> converted/security-converted.sql
8. ROLE on operator (grant, converted) -> converted/security-converted.sql
9. SALES.CUSTOMER (table, converted) -> converted/tables-converted.sql
10. SALES."ACCOUNT" (table, converted) -> converted/tables-converted.sql
11. SALES.BRANCH (table, converted) -> converted/tables-converted.sql
12. SALES.EMPLOYEE (table, converted) -> converted/tables-converted.sql
13. SALES.PRODUCT (table, converted) -> converted/tables-converted.sql
14. SALES.ORDER\_HEADER (table, converted) -> converted/tables-converted.sql
15. SALES.ORDER\_LINE (table, converted) -> converted/tables-converted.sql
16. SALES.PAYMENT (table, converted) -> converted/tables-converted.sql
17. SALES.LOAN (table, converted) -> converted/tables-converted.sql
18. SALES.LOAN\_PAYMENT (table, converted) -> converted/tables-converted.sql
19. SALES.CARD (table, converted) -> converted/tables-converted.sql
20. SALES.CARD\_TXN (table, converted) -> converted/tables-converted.sql
21. SALES.TRANSFER (table, converted) -> converted/tables-converted.sql
22. SALES.AUDIT\_LOG (table, converted) -> converted/tables-converted.sql
23. SALES.RISK\_SCORE (table, converted) -> converted/tables-converted.sql
24. SALES.ACCOUNT\_SNAPSHOT (table, converted) -> converted/tables-converted.sql
25. SALES.CUSTOMER\_SEGMENT (table, converted) -> converted/tables-converted.sql
26. SALES.EXTERNAL\_RATE (table, converted) -> converted/tables-converted.sql
27. SALES.DAILY\_BALANCE (table, converted) -> converted/tables-converted.sql
28. SALES.RECOVERED\_TABLE (table, converted) -> converted/tables-converted.sql
29. SALES.IX\_CUSTOMER\_STATUS (index, converted) -> converted/indexes-converted.sql
30. SALES.IX\_ACCOUNT\_CUSTOMER (index, converted) -> converted/indexes-converted.sql
31. SALES.HIX\_ACCOUNT (hash\_index, converted) -> converted/indexes-converted.sql
32. SALES.HIX\_CARD (hash\_index, converted) -> converted/indexes-converted.sql
33. SALES.JIX\_ORDER (join\_index, converted) -> converted/indexes-converted.sql
34. SALES.CUSTOMER\_ALIAS (synonym, converted) -> converted/indexes-converted.sql
35. LOAD\_SCRIPT.SQL (script, converted) -> converted/load\_script-converted.sql
36. SALES.ADD\_PAYMENT (procedure, converted) -> converted/routines-converted.sql
37. SALES.UPDATE\_BALANCE (procedure, converted) -> converted/routines-converted.sql
38. SALES.POST\_TRANSFER (procedure, converted) -> converted/routines-converted.sql
39. SALES.CAPTURE\_SNAPSHOT (procedure, converted) -> converted/routines-converted.sql
40. SALES.REPAIR\_PAYMENT (procedure, converted) -> converted/routines-converted.sql
41. SALES.REFRESH\_DAILY (macro, converted) -> converted/routines-converted.sql
42. SELECT, INSERT on sales.payment (grant, converted) -> converted/security-converted.sql
43. SELECT on sales.audit\_log (grant, converted) -> converted/security-converted.sql
44. EXECUTE PROCEDURE on sales.add\_payment (grant, converted) -> converted/security-converted.sql
45. SALES.CUSTOMER\_ACCOUNTS (view, converted) -> converted/views-converted.sql
46. SALES.ACCOUNT\_PAYMENTS (view, converted) -> converted/views-converted.sql
47. SALES.CUSTOMER\_LOANS (view, converted) -> converted/views-converted.sql
48. SALES.ORDER\_DETAIL (view, converted) -> converted/views-converted.sql
49. SALES.CARD\_ACTIVITY (view, converted) -> converted/views-converted.sql
50. SALES.TRANSFER\_ACTIVITY (view, converted) -> converted/views-converted.sql
51. SALES.CUSTOMER\_RISK (view, converted) -> converted/views-converted.sql
52. SALES.DAILY\_ACCOUNT\_BALANCES (view, converted) -> converted/views-converted.sql
53. SALES.CUSTOMER\_EXTERNAL\_RATE (view, converted) -> converted/views-converted.sql
54. SALES.UNUPLOADED\_REFERENCE (view, converted) -> converted/views-converted.sql
55. SELECT on sales.customer\_accounts (grant, converted) -> converted/security-converted.sql
56. SELECT on sales.account\_payments (grant, converted) -> converted/security-converted.sql

Next steps:

- Review the expert-review items: their converted files carry a `NEEDS-REVIEW` header.
- Replace every masked placeholder (`***MASKED***`) with a secret reference for the target.
- Run the runner in execution/snowflake/ first in its default dry-run mode, which prints the ordered plan without connecting; then rerun with `--execute`.
- Add `--include-review` only after the expert-review files are approved.
- MigraAI never connects to the target; the runner uses the target CLI's own config.
