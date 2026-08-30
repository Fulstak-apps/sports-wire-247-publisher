# Crash Out Sports 24/7 source monitor

This directory defines the monitoring contract for Crash Out Sports. It normalizes and deduplicates events, verifies claims and hands qualified stories to the publisher queue.

Source tiers:

1. Official leagues, teams, schools, transaction wires, athlete and agent statements.
2. Established national and local sports reporting with named reporters and direct sourcing.
3. Verified social posts and specialist outlets as leads that still require independent confirmation.
4. Viral and amateur submissions only when the event, date, identity, location and media rights can be established.

Important: this repository does not contain Instagram login credentials. The always-on monitor must use an authorized data source/API or permitted public-feed provider and store secrets only in the hosting provider's secret manager. It must not scrape around access controls or use private account credentials.

Runtime contract:
1. Poll on a short interval.
2. Persist a cursor per source.
3. Normalize links, entities, timestamps, and claim fingerprints.
4. Deduplicate across sources and against the last 72 hours of Crash Out Sports posts.
5. Confirm every core claim with at least two credible sources.
6. Obtain a documented reuse-permitted image.
7. Write one queue item with source handle, subject handle, source URL, rights basis, verification notes, graphic asset, caption, and publish-after time.
8. Publisher handles Instagram + Threads delivery.
9. Retry transient errors with exponential backoff and never duplicate an event.
10. Quarantine high-risk or unverified claims and protect minors.
