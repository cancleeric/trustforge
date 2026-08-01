# Document and communication control

Status: **draft / unapproved / non-effective**

## Controlled document metadata

Every controlled AIMS document records: title, document ID, version, owner,
status, approvers, approval date, next review date, classification, retention,
superseded version, change rationale, and evidence URI. Allowed lifecycle:
`draft -> in-review -> approved -> obsolete`. Only an authorised approver may
advance a document; Git history alone is change evidence, not approval evidence.

Obsolete documents remain retrievable for their retention period, are visibly
marked obsolete, and cannot be used as the current operating instruction.
Restricted evidence must be linked by an access-controlled URI and never copied
into this repository to make a link appear complete.

## Communication matrix

| Event | Audience | Owner | Approver | Channel | Time limit | Evidence |
|---|---|---|---|---|---|---|
| material AI incident | affected internal owners; external parties as legally required | Security owner | CEO + Compliance | approved incident channel | per approved incident classification; TBD until approved | incident record URI |
| material risk acceptance | accountable executive and affected control owners | Risk owner | authorised risk accepter | controlled decision record | before operation under accepted risk | decision URI |
| AIMS policy/control change | affected roles | document owner | policy approver | controlled release notice | before effective date | notice + acknowledgement URI |
| external certification/compliance claim | intended external audience | Product owner | Compliance + CEO | approved publication channel | before publication | exact approved text URI |

No external statement may say or imply that TrustForge is certified, compliant,
conformant, CE marked, or entitled to a presumption of conformity unless the
specific statement and supporting status are approved and current.

## Demonstration record

The first lifecycle demonstration remains `TBD`: select one low-risk document,
record draft and in-review revisions, obtain authorised approval, then supersede
it with a second version and retain an obsolete marker. Until those dated records
exist, this control is planned, not implemented.
