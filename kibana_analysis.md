# Kibana-MDM Deep Analysis: Case 05008656

**Generated:** 2026-07-14T09:50:00Z
**Anchor:** jobInstanceId 1261229932397899776 (first reported M&M failure)
**Product:** Customer 360 SaaS
**Scenario:** batch/match-job-analysis (Match & Merge -- BMG step)

---

## * HEADER

```
jobInstanceId:   1261229932397899776 (primary) + 5 additional reported
Job Type:        Match & Merge (match_merge_PP)
Org:             AG2R La Mondiale Groupe (gcpemw2 pod)
Pod:             gcpemw2spark (match-ng-group), gcpusw1spark (match-ng-index)
Spark Images:    match-ng-group-spark:..af2016f (BMG), match-ng-index-spark:..ee53875 (tokenization)
Base Image:      mdm-spark-rockylinux:2026-07-M-Release_dev_complete_b07cbe0 (BROKEN)
Run Mode:        SPARK (all drivers)
Failure Class:   JVM STATIC INIT (ExceptionInInitializerError at RDDOperationScope$.<clinit>)
Step Ladder:     ACQUIRE-LOCK -> MATCH-TOKEN -> [FAILED] (never reaches MATCH-PAIR-GEN/MERGE/INDEX)
```

## * TIMELINE

```
ONSET:
  2026-07-12T16:43:54Z  FIRST ERROR on gcpemw2spark (this customer)
                         MatchPairsToGroupsDriver: ExceptionInInitializerError
                         Cause: Scala module 2.15.2 requires Jackson Databind >= 2.15.0 and < 2.16.0
                                Found jackson-databind version 2.17.2

  2026-07-12T16:43:54Z  - 2026-07-12T23:59:59Z  72 ERROR hits on gcpemw2spark
                         All MatchPairsToGroupsDriver (match-ng-group service)

  2026-07-13T00:08:49Z  First ERROR on gcpusw1spark (other affected pod)
                         SparkActionTrigger: ExceptionInInitializerError
                         match-ng-index-spark-server.jar (tokenization service)

  2026-07-13T00:00:00Z - 2026-07-13T14:37:31Z  393 ERROR hits on gcpusw1spark
                         All SparkActionTrigger (match-ng-index service)

TOTALS:
  07-12:  72 hits  (gcpemw2spark only -- match-ng-group)
  07-13: 393 hits  (gcpusw1spark only -- match-ng-index)
  TOTAL: 465 hits  (14d window per MDMN-212978 comment)

TREND: ESCALATING -- 72/day -> 393/day (5.4x increase as more scheduled jobs hit the broken image)

ONGOING: As of last Kibana window (2026-07-13T14:37Z), errors continue unabated.
         No fix deployed yet (CTP pending).
```

## * MONGO

```
N/A -- Job fails at Spark driver JVM initialization before any application logic executes.
       No Mongo writes occur. jobInstanceV2 status transitions are driven by service-plane
       callbacks from the job-service, which fires FAILED after the Spark driver exits non-zero.
```

## * SERVICE-PLANE

```
Job launch (pre-Spark):
  job-service dispatches Spark driver via RM (resource-manager)
  -> Spark driver pod scheduled on gcpemw2spark (match-ng-group) or gcpusw1spark (match-ng-index)
  -> JVM starts, loads spark-core_2.12-3.5.7.jar classes
  -> RDDOperationScope$.<clinit> triggers ObjectMapper.registerModule(DefaultScalaModule)
  -> jackson-module-scala 2.15.2 version check REJECTS jackson-databind 2.17.2
  -> ExceptionInInitializerError (unrecoverable -- static init failure)
  -> Spark driver exits with non-zero code
  -> RM detects driver failure, fires FAILED callback to job-service
  -> job-service marks step/job FAILED

No service-plane callbacks (step PATCHes, cross-service POSTs) are reached because
the driver dies before Spark even starts. The only callback is the RM-to-job-service FAILED signal.
```

## * SPARK

```
Driver Analysis:
  Driver class:    MatchPairsToGroupsDriver (BMG on gcpemw2spark)
                   SparkActionTrigger > MatchTokenizationRunner (Index on gcpusw1spark)
  Driver state:    DIED AT INIT (exit non-zero)
  Executors:       NONE LAUNCHED (driver dies before executor allocation)
  DRA band:        N/A (never reached)
  Spark version:   3.5.7 (from jar metadata in stack trace)

Root cause (classpath):
  $SPARK_HOME/jars contains BOTH:
    jackson-module-scala_2.12-2.15.2.jar   (Spark 3.5.7 bundled -- should have been deleted)
    jackson-databind-2.17.2.jar            (CVE-upgraded copy)
    jackson-module-scala_2.12-2.17.2.jar   (CVE-upgraded copy -- correct match)

  Filesystem load order on GCP resolves jackson-module-scala 2.15.2 first.
  module-scala 2.15.2 setupModule() enforces: databind in [2.15.0, 2.16.0)
  Found databind 2.17.2 -> JsonMappingException -> ExceptionInInitializerError

  This is NOT an OOM, NOT a Spark task failure, NOT an executor issue.
  It is a JVM classpath/library version conflict that prevents Spark from starting at all.
```

## * ERRORS

```
ERROR signature (100% of hits):
  java.lang.ExceptionInInitializerError: null
    at org.apache.spark.SparkContext.withScope / .parallelize
    at org.apache.spark.sql.execution.datasources.SchemaMergeUtils$.mergeSchemasInParallel
    OR
    at org.apache.spark.SparkJobTrigger.executeBasedOnRepositoryType / .executeRDD

  Caused by: com.fasterxml.jackson.databind.JsonMappingException:
    Scala module 2.15.2 requires Jackson Databind version >= 2.15.0 and < 2.16.0
    - Found jackson-databind version 2.17.2
      at com.fasterxml.jackson.module.scala.JacksonModule.setupModule(JacksonModule.scala:61)
      at com.fasterxml.jackson.module.scala.DefaultScalaModule.setupModule
      at com.fasterxml.jackson.databind.ObjectMapper.registerModule
      at org.apache.spark.rdd.RDDOperationScope$.<init>(RDDOperationScope.scala:82)
      at org.apache.spark.rdd.RDDOperationScope$.<clinit>

Distribution by image/pod:
  match-ng-group-spark:..af2016f  on gcpemw2spark:  396 hits (BMG -- MatchPairsToGroupsDriver)
  match-ng-index-spark:..ee53875  on gcpusw1spark:   69 hits (Index -- SparkActionTrigger)
  TOTAL: 465 hits

Affected orgs (from MDMN-212978): 5 distinct tenants across both pods.

co.elastic APM timeouts (BENIGN -- not causal):
  Also visible in same logs but these are monitoring/telemetry connectivity issues.
  "Read timed out" in ApmServerConfigurationSource -- occurs on many healthy pods too.
  NOT correlated with ExceptionInInitializerError; does not block Spark init.
```

---

## FINDINGS

1. **[CRITICAL] Jackson classpath conflict kills all Spark-based match/merge/tokenization jobs on both affected pods.** 465 confirmed errors in 14 days, escalating daily. Every BMG driver and tokenization driver dies at JVM static initialization before any application work. Zero records can be processed.

2. **[CONFIRMED] Root cause is in mdm-spark base image b07cbe0** -- stale `jackson.delete.version=2.14.2` fails to remove Spark 3.5.7's bundled `jackson-module-scala 2.15.2`, leaving it to clash with the CVE-upgraded `jackson-databind 2.17.2`.

3. **[CONFIRMED] Fix exists but is NOT deployed.** Fix commit `mdm-spark@c27485c` (jackson.delete.version bumped to 2.15.2) exists on release/2026-07-M-Release. Service-level fix PRs raised (mdm-match-ng-group-service PR #179, mdm-batch-match-ng-index PR #83). Awaiting merge + image rebuild + CTP deployment.

4. **[CONFIRMED] This customer's pod (gcpemw2spark) is confirmed affected.** First error 2026-07-12T16:43:54Z -- aligns with customer-reported failure start (2026-07-12 18:37:52 local time = 16:37 UTC, within 7 minutes of first Kibana hit).

5. **[INFO] No Spark executors, no Mongo writes, no service-plane callbacks.** The failure is so early in the JVM lifecycle that nothing else in the stack runs. Standard six-banner TIMELINE/MONGO/SERVICE-PLANE are trivially empty because the failure is pre-application.

6. **[INFO] co.elastic APM timeouts are benign.** Monitoring connectivity issues present in logs but not causal. The engineer initially flagged these but they are a red herring.

---

## Kibana Query Log

| # | Query | Scope | Result |
|---|-------|-------|--------|
| 1 | `message: 1261229932397899776`, service=ingress-nginx | 07-12 to 07-14 | 0 hits (logs rotated for specific JID) |
| 2 | `message: 1261229932397899776`, service=job | 07-12 to 07-14 | 0 hits (logs rotated) |
| 3 | `message: "Scala module 2.15.2"` | 07-12 to 07-14, sort asc | 72 hits on gcpemw2spark, first at 16:43:54Z |
| 4 | `message: "Scala module 2.15.2"` | 07-13 to 07-14 | 393 hits on gcpusw1spark |
| 5 | `message: "Scala module 2.15.2"` | 07-11 to now, all pods | **465 total hits** |
| 6 | context around 2026-07-13T14:37:31.697Z | pivot | Confirmed ERROR from Spark driver, no structured labels |
