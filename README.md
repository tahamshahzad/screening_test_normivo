# Request Management Module (RMM)

A Frappe v16 app for managing Compassionate Use Requests. External physicians submit requests for unapproved medicinal products and an internal pharma review team evaluates and decides on them.

---

## Bench & Python Version

| Requirement | Version |
|---|---|
| Frappe | v16 |
| Python | 3.14 |

---

## Installation

```bash
bench get-app https://github.com/tahamshahzad/screening_test_normivo
bench --site <site> install-app rmm
bench --site <site> migrate
```

`bench migrate` imports the bundled fixtures (roles, workflow, workflow states, workflow actions, and seed users) automatically. No extra command needed.

> **Note on seed user passwords:** Frappe does not export password hashes in fixtures. Seed accounts will be created on install but passwords need to be set via the "Forgot Password" flow or `bench --site <site> set-user-password <email>`.

### Seed Users

| Role | Email |
|---|---|
| RMM Team Lead | rmm_team_lead@yopmail.com |
| RMM Requestor | requestor@yopmail.com |
| RMM Requestor | requestor_2@yopmail.com |
| RMM Agent (Reviewer) | review_agent@yopmail.com |
| RMM Agent (Reviewer) | review_agent_james@yopmail.com |
| RMM Medical Reviewer | medical_verification_agent@yopmail.com |

---

## Story 1 - Creating a Request

### Fields chosen

| Section | Field | Type | Rationale |
|---|---|---|---|
| **Requester** | `requester_name` | Data | Who is submitting the request |
| | `medical_license_number` | Data | Confirms the requester is a licensed practitioner |
| | `speciality` | Select | Gives context on the clinical background of the request |
| | `institution_name` | Data | The organisation the doctor is from |
| | `institution_type` | Select | University hospital vs clinic - relevant for ethics review |
| | `requester_email` | Data | For follow-up communication |
| **Patient** | `patient_code` | Data | Anonymised patient identifier |
| | `patient_initials` | Data | Secondary reference for de-identification |
| | `patient_year_of_birth` | Int | Age range without storing exact date of birth |
| | `sex` | Select | Clinically relevant, also needed for the pregnancy flag |
| | `indication_code` | Data | ICD or similar standardised code for the condition |
| | `indication_description` | Text Editor | Plain-language description of the condition |
| **Clinical Request** | `requested_product` | Data | The unapproved product being requested |
| | `requested_dose` | Data | Dose and regimen |
| | `treatment_duration` | Data | How long treatment is expected to run |
| | `clinical_justification` | Text Editor | Why this product and why now |
| | `prior_treatments` | Text Editor | What has already been tried - key for the "no approved alternative" criterion |
| **Clinical Criteria** | `is_off_label` | Check | Flags unapproved use of an approved product |
| | `is_pediatric` | Check | Paediatric patient - needs closer review |
| | `is_pregnancy` | Check | Triggers mandatory medical verification before a decision can be made |
| | `requires_verification` | Check (read-only) | Auto-set from `is_pregnancy`, drives the verification guard |
| **Decision** | `decision_date` | Datetime | Stamped when a decision is made |
| | `decision_rationale` | Text Editor | The basis for the approval or rejection |
| | `bfarm_program_number` | Data | Reference to the relevant regulatory programme (BfArM) |
| **System** | `received_on` | Date (read-only) | Auto-stamped on creation via `before_save` |
| | `naming_series` | Select | Auto-generates `CUR-YYYY-####` IDs |

### Why these fields

The form covers three parties involved in a compassionate use request:

1. **Patient (third party)** - identified by code and initials, not name. Year of birth and sex give reviewers enough context without storing unnecessary personal data.
2. **Requestor (external physician)** - name, licence number, specialty, and institution show the review team who is asking and whether they are qualified. The doctor is not a system user; the form is how they submit the request.
3. **Review team (internal)** - the clinical criteria checkboxes (off-label, paediatric, pregnancy) let the doctor flag risk signals directly on the form. The decision section is filled in by the review team after they reach a conclusion.

`prior_treatments` is required because "no approved alternative exists" is the main qualifying criterion for compassionate use. `indication_code` combined with `indication_description` lets reviewers reference a standardised code while also reading a plain explanation.

**Who is internally responsible:** Tracked via Frappe's native assignment field (`_assign`). The Team Lead assigns a request to an agent using the built-in Assign button. That agent's email is used by both the permission filter and the workflow.

**Note on field completeness:** This field set is a best-effort approximation based on the domain description. I am not a domain expert in pharmaceutical regulation. With a proper clinical or regulatory brief, the fields would be updated to match the exact requirements of the applicable programme (e.g. BfArM §21 AMG in Germany).

### Field-level access (permlevels)

Frappe's `permlevel` system assigns a number to each field and a matching number to each role's permission row. A role can only read or write fields at or below its level.

| Permlevel | Fields | Who can write |
|---|---|---|
| **3** | Doctor-facing fields (requester, patient, clinical request, clinical criteria) | RMM Requestor |
| **4** | Decision fields (decision date, rationale, BfArM number) | RMM Agent, RMM Medical Reviewer |
| **1** | Naming series, `requires_verification` (read-only) | System / auto-set |

The doctor can read the decision fields once they are filled in but cannot edit them. The review team can read what the doctor entered but cannot change it after submission. Frappe enforces this at the model level - no custom code needed.

### Roles and visibility

| Role | Create | Read | Write |
|---|---|---|---|
| **RMM Requestor** | Yes - own requests only | Own requests only | Doctor-facing fields (permlevel 3) |
| **RMM Team Lead** | No | All requests | All requests; assigns agents |
| **RMM Agent** | No | Requests assigned to them | All fields including decision (permlevel 4) |
| **RMM Medical Reviewer** | No | Requests in Verification, Approved, Rejected (plus any assigned to them) | Decision fields (permlevel 4) |

**How visibility is enforced:**

- **RMM Requestor** - Frappe's built-in `if_owner` flag on the DocType permission row limits them to records they created. No custom code needed.
- **RMM Team Lead** - the `permission_query_conditions` hook returns no SQL filter for this role, so they see everything.
- **RMM Agent** - the hook filters to records where their email is in the `_assign` field. They only see what has been assigned to them.
- **RMM Medical Reviewer** - the hook adds `workflow_state IN ('Verification', 'Approved', 'Rejected')` so they only see records that have reached those states. If a record is explicitly assigned to them, they can see it regardless of state.

Both a list-view SQL filter hook (`permission_query_conditions`) and a document-level access hook (`has_permission`) are registered in `hooks.py` so the rules apply whether a user is on the list view or opens a record directly.

---

## Story 2 - Processing a Request Through Internal Review

### States and transitions

![Compassionate Use Request Workflow](docs/workflow.png)

| From | Action | To | Role |
|---|---|---|---|
| New | Start Review | In Review | RMM Agent |
| In Review | Needs Verification | Verification | RMM Agent |
| In Review | Needs Correction | Awaiting Requestor | RMM Agent |
| In Review | Approve | Approved | RMM Agent |
| In Review | Reject | Rejected | RMM Agent |
| Awaiting Requestor | Resubmit Request | In Review | RMM Requestor |
| Verification | Approve | Approved | RMM Medical Reviewer |
| Verification | Reject | Rejected | RMM Medical Reviewer |

### How the workflow works

A request starts in **New** when the doctor submits it. The Team Lead assigns it to an agent, who moves it to **In Review**. From there the agent has four options:

- **Approve or Reject** directly - for requests that do not need medical verification.
- **Needs Verification** - escalates to a Medical Reviewer. Only the Medical Reviewer can Approve or Reject from the **Verification** state.
- **Needs Correction** - sends it back to the doctor. The request enters **Awaiting Requestor** and the doctor can update it and resubmit, which puts it back into **In Review** only. It cannot skip ahead to any other state.

### Decision: Workflow Action transitions, not custom code

All transitions including the send-back loop are built as Workflow Action transitions inside Frappe's Workflow engine, with each action restricted to the correct role. This means:

- Only an assigned agent (RMM Agent role) can trigger "Needs Correction".
- "Awaiting Requestor" has only one exit - "Resubmit Request" back to "In Review" (RMM Requestor role). There is no other transition out of that state, so skipping ahead is not possible.
- No `frappe.db.set_value` or manual state manipulation is used anywhere. Frappe's workflow engine handles all transition rules and logs every state change with the user and timestamp automatically.

### Decision: Verification approach

**I chose approach (a) - a boolean flag controls whether Verification is required - with the state always present in the workflow.**

The Verification state is always in the workflow definition. Whether a request has to go through it is controlled by `requires_verification`, which is auto-set from `is_pregnancy` in `before_save`. In practice:

- If verification is not required, the agent can Approve or Reject directly from In Review. The Verification state exists but is never used.
- If verification is required, the agent must use "Needs Verification" to escalate. The `validate()` method then blocks any attempt to Approve or Reject unless the last saved state in the database was "Verification".

I chose this over approach (b) (auto-skip) because Frappe's Workflow engine does not support conditional state skipping natively. Making it auto-skip would need a custom `before_workflow_action` hook to intercept and re-route transitions - more code than the boolean flag approach. The flag is simpler, explicit, and easy to test.

**Two layers of enforcement:**

1. **UI layer** - transitions are tied to roles, so an agent on a pregnancy request will not see direct Approve/Reject buttons. Only "Needs Verification" is available as the escalation path.

2. **Backend guard** - even if someone calls the API directly and tries to force the state to Approved or Rejected, `validate()` blocks the save:

```python
def validate(self) -> None:
    if (
        self.workflow_state in ("Approved", "Rejected")
        and self.requires_verification
        and (self.get_db_value("workflow_state") or "New") != "Verification"
    ):
        frappe.throw(
            "This request requires medical verification before it can be approved or rejected."
        )
```

`get_db_value("workflow_state")` reads the last committed state from the database, so the guard only fires when Verification was skipped. Once a request has gone through Verification, the guard allows the final decision.

---

## Story 3 - Managing the Queue

**Delivered via Frappe core - no custom code needed.**

Frappe's built-in list view covers what Story 3 needs. Agents can:

- See all requests assigned to them (the `permission_query_conditions` hook hides everything else automatically).
- Filter by `workflow_state`, `received_on`, `requested_product`, or any other field using the standard filter bar.
- Sort by date or status to prioritise work.
- Save named filters as private views.

I did not build a custom queue view because the native list view with the permission filter already delivers the outcome. Building a custom page would have duplicated it for no extra value.

---

## Story 4 - Workload Overview

**Skipped - can be done with a Frappe Script Report, no DocType changes needed.**

A Team Lead workload view (requests per agent broken down by status) can be built as a Frappe Script Report on the `Compassionate Use Request` DocType. The report queries `_assign`, `workflow_state`, and `modified` and groups by assignee and state. This is report configuration, not controller code.

I skipped it to focus on Stories 1 and 2. In a real sprint this would be next.

---

## Stories Completed / Skipped

| Story | Status | Notes |
|---|---|---|
| Story 1 - Creating a request | Complete | Full field set, `received_on` auto-stamp, native `_assign` for responsibility, role-based access with permlevels |
| Story 2 - Processing through review | Complete | Six states, eight role-restricted transitions; send-back loop via Workflow engine; Verification gated by boolean flag + `validate()` guard |
| Story 3 - Managing the queue | Complete via Frappe core | Native list view + `permission_query_conditions` hook gives agents a filtered queue with sorting and filtering |
| Story 4 - Workload overview | Skipped | Achievable as a Frappe Script Report; skipped to prioritise Stories 1 and 2 |

---

## How to Extend

### 1. SLA Timers Per Status

- Add a `sla_deadline` Datetime field and a config doctype (`RMM SLA Config`) mapping each workflow state to a number of business hours.
- Use `doc_events → on_update` to stamp `sla_deadline = now + configured_hours` whenever `workflow_state` changes.
- A scheduled task (hourly) checks for records where `sla_deadline < now` and the state is still open, then sends an alert via `frappe.sendmail` and sets an `sla_breached` flag.
- The list view can highlight breached records using a `doctype_list_js` renderer checking `sla_breached`.
- No external dependency needed - all within Frappe's scheduler and email setup.

### 2. Bulk Reassignment by Team Leads

- Add a list-view action (via `doctype_list_js`) visible only to the `RMM Team Lead` role that opens a dialog to pick a new assignee.
- On submit, a whitelisted server method loops through the selected records, removes the existing `_assign` entry via `frappe.utils.assign_to`, and adds the new one.
- Send one batch notification to the new assignee instead of one email per record.
- Guard the method with `frappe.only_for("RMM Team Lead")` to prevent misuse.
- No schema changes needed - `_assign` is a native Frappe field.

### 3. Audit Export for Compliance

- `track_changes = 1` is already on the DocType, so Frappe's `Version` log records every field change with timestamp and user.
- Add a Script Report joining `Compassionate Use Request` with `Version` to produce a per-request audit trail showing who changed what and from which state.
- Export to XLSX via Frappe's built-in report export. For automated delivery, a scheduled task can generate and email the report to a compliance mailbox.
- Patient fields (permlevel 3) can be redacted in the export query using `frappe.has_permission` checks per row, keeping the export consistent with the UI access model.
- Write each export to a Frappe `File` record for long-term archiving.

---

## Running Tests

```bash
bench --site <site> run-tests --app rmm
# or target the specific doctype:
bench --site <site> run-tests --doctype "Compassionate Use Request"
```

Tests are in `rmm/request_management_module/doctype/compassionate_use_request/test_compassionate_use_request.py` and cover:

- **Group 1 - Before Save**: `received_on` auto-stamp, `requires_verification` set from `is_pregnancy`, initial workflow state.
- **Group 2 - List Filter**: Each role's `permission_query_conditions` output - agents see only assigned records, medical reviewers see only allowed states, team leads see everything.
- **Group 3 - Document Access**: `has_permission` for each role and workflow state.
- **Group 4 - Workflow Guard**: Transitions via `apply_workflow` (not `db.set_value`); confirms the guard blocks direct Approve/Reject when verification is required and allows it after Verification.

---

## Time Log

| Story | Approx. time |
|---|---|
| Story 1 - Field set, DocType, roles, and permissions | 3 h |
| Story 2 - Workflow setup, verification guard | 3 h |
| Story 3 - Queue (Frappe core; permission filter) | included in Story 1 |
| Story 4 - Workload overview | skipped |
| Tests, type hints, mypy/Pyright config | 2 h |
| **Total** | **8 h** |

Time includes reading Frappe v16 docs, debugging workflow action names, and sorting out type-checker config for Frappe's untyped internals.

---

## License

MIT
