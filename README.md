# Request Management Module (RMM)

A Frappe v16 app implementing a **Compassionate Use Request** workflow — enabling external physicians to submit requests for unapproved medicinal products, which an internal pharma review team then evaluates and decides on.

---

## Bench & Python Version

| Requirement | Version |
|---|---|
| Frappe | v16 |
| Python | 3.14 |

---

## Installation

```bash
bench get-app https://github.com/<your-org>/rmm
bench --site <site> install-app rmm
bench --site <site> migrate
```

`bench migrate` imports the bundled fixtures (roles, workflow, and seed users) automatically. No additional seed command is needed.

---

## Story 1 — Field Set

### Fields chosen

| Section | Field | Type | Rationale |
|---|---|---|---|
| **Requester** | `requester_name` | Data | Identifies the submitting physician |
| | `medical_license_number` | Data | Verifies the requester is a licensed practitioner |
| | `speciality` | Select | Contextualises the clinical framing of the request |
| | `institution_name` | Data | Establishes the requesting organisation |
| | `institution_type` | Select | Distinguishes university hospitals from clinics — relevant for ethics review |
| | `requester_email` | Data | Communication channel for follow-up |
| **Patient** | `patient_code` | Data | Anonymises the patient while allowing tracking |
| | `patient_initials` | Data | Secondary de-identification reference |
| | `patient_year_of_birth` | Int | Age range without exposing exact DOB |
| | `sex` | Select | Clinically relevant; required for pregnancy flag logic |
| | `indication_code` | Data | ICD or similar code for the condition |
| | `indication_description` | Text Editor | Free-text clinical description |
| **Clinical Request** | `requested_product` | Data | The unapproved product being requested |
| | `requested_dose` | Data | Dose and regimen |
| | `treatment_duration` | Data | Intended duration |
| | `clinical_justification` | Text Editor | Why this product; why now |
| | `prior_treatments` | Text Editor | What has already been tried — key for the "no approved alternative" criterion |
| **Clinical Criteria** | `is_off_label` | Check | Flags unapproved use of an approved product |
| | `is_pediatric` | Check | Flags paediatric patient; heightened scrutiny |
| | `is_pregnancy` | Check | Triggers mandatory medical verification before approval |
| | `requires_verification` | Check (read-only) | Auto-set from `is_pregnancy`; drives the verification guard |
| **Decision** | `decision_date` | Datetime | Stamped when a decision is reached |
| | `decision_rationale` | Text Editor | Reviewers document the basis for approval or rejection |
| | `bfarm_program_number` | Data | Reference to the relevant regulatory programme (BfArM) |
| **System** | `received_on` | Date (read-only) | Auto-stamped on creation; audit anchor |
| | `naming_series` | Select | Auto-generates `CUR-YYYY-####` document IDs |

### Why these fields

The framework models three distinct parties:

1. **Patient (third party)** — identified only by anonymised code, initials, year of birth, and sex, satisfying data-minimisation requirements while still giving reviewers the clinical context they need.
2. **Requestor (external professional)** — name, licence number, specialty, and institution establish identity and credibility without requiring the physician to be a system user.
3. **Review team (internal)** — the clinical criteria flags (off-label, paediatric, pregnancy) encode risk signals directly on the form so reviewers do not have to derive them from free text. Decision fields are at a higher `permlevel` so only reviewers can write them.

The `indication_code + indication_description` pair lets reviewers cross-reference a standardised code while still reading a human explanation. `prior_treatments` is the single most important qualifying criterion for compassionate use — "no approved alternative" — so it is a required field.

---

## Story 2 — Role-Based Access

### Roles

| Role | Create | Read | Write | Notes |
|---|---|---|---|---|
| **RMM Requestor** | Yes | Own records only | Own records | `if_owner` flag on DocType permission row; no extra SQL filter needed |
| **RMM Team Lead** | No | All records | All records | Can assign requests to reviewers via native Frappe assignment |
| **RMM Medical Reviewer** | No | Verification / Approved / Rejected only | Decision fields only | Further filtered by `permission_query_conditions` |
| **RMM Agent** | No | All records | All records | Internal processing role |

### Decision: `permission_query_conditions` + `has_permission`

Frappe's DocType-level `if_owner` flag handles Requestor visibility natively — no custom SQL needed. For Medical Reviewers, a custom `permission_query_conditions` hook injects a `workflow_state IN (...)` SQL filter on the list view, and a `has_permission` hook gates document-level access. Both hooks also check `_assign` so that any explicitly assigned record is always visible to the assignee regardless of role.

Team Lead visibility is handled by returning an empty string (no filter) from both hooks, giving them full list and document access.

---

## Story 3 — Workflow

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

### Decision: workflow-level role assignment

Each workflow transition is restricted to the appropriate role inside the Frappe Workflow document itself. This means no custom Python is needed to block unauthorised transitions — Frappe enforces them declaratively. The only custom logic is the verification guard in `validate()` (see Story 4).

---

## Story 4 — Verification Stage

**Implemented using Frappe core — no separate hook file required.**

The `Verification` state and its transitions are defined entirely within Frappe's built-in Workflow engine. The transitions `Needs Verification`, `Approve`, and `Reject` from the Verification state are restricted to the appropriate roles without any custom hook.

### Verification guard

One piece of custom logic was added in `validate()` to prevent approving or rejecting a request that requires verification while bypassing the Verification state:

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

`get_db_value("workflow_state")` reads the **previous** committed state from the database. The guard fires only when the transition skips Verification. Once a request has legitimately been in the Verification state, the guard allows the final Approve/Reject.

`requires_verification` is auto-set in `before_save` from `is_pregnancy`. The field is read-only so reviewers cannot override it.

---

## Stories Completed / Skipped

| Story | Status | Notes |
|---|---|---|
| Story 1 — Field set & DocType | Complete | All fields, permlevels, and naming series configured |
| Story 2 — Role-based access | Complete | Four roles; `permission_query_conditions` and `has_permission` hooks |
| Story 3 — Workflow | Complete | Five states, six actions, role-restricted transitions |
| Story 4 — Verification stage | Complete via Frappe core | Workflow state + `validate()` guard; no separate hook file needed |

---

## How to Extend

### 1. SLA Timers Per Status

- Add a `sla_deadline` Datetime field (read-only, high permlevel) and a configuration doctype (`RMM SLA Config`) mapping each workflow state to a number of business hours.
- Hook `doc_events → on_update` to stamp `sla_deadline = now + configured_hours` whenever `workflow_state` changes.
- A scheduled task (`scheduler_events`, hourly) queries records where `sla_deadline < now` and the state is still open, then sends alerts via `frappe.sendmail` and sets an `sla_breached` flag.
- The list view can colour-code breached records using a `doctype_list_js` client-side renderer checking `sla_breached`.
- No external dependency required — all within Frappe's scheduler and email infrastructure.

### 2. Bulk Reassignment by Team Leads

- Add a list-view action (registered in `doctype_list_js`) that appears only for the `RMM Team Lead` role, opening a dialog to select a new assignee.
- On submit, call a whitelisted server method that iterates the selected document names, removes the existing `_assign` entry via `frappe.utils.assign_to`, and adds the new assignee.
- The same method sends a single batch notification to the new assignee rather than one email per record.
- Gate the whitelisted method with `frappe.only_for("RMM Team Lead")` to prevent role escalation.
- No schema changes needed — `_assign` is a native Frappe field on every DocType.

### 3. Audit Export for Compliance

- `track_changes = 1` is already enabled on the DocType, so Frappe's `Version` log captures every field change with timestamp and user.
- Add a Script Report that joins `Compassionate Use Request` with `Version` to produce a chronological audit trail per request: who changed what, when, and from which workflow state.
- Export the report to XLSX via Frappe's built-in report export; for automated delivery add a scheduled task that generates and emails the report to a compliance mailbox on a configurable cadence.
- Sensitive fields (patient data at permlevel 3) can be redacted in the export query using `frappe.has_permission` checks per row, ensuring the export respects the same access model as the UI.
- For long-term retention, write each export to a Frappe `File` record so every export is archived and traceable.

---

## Running Tests

```bash
bench --site <site> run-tests --app rmm
# or target the specific doctype:
bench --site <site> run-tests --doctype "Compassionate Use Request"
```

Tests are in `rmm/request_management_module/doctype/compassionate_use_request/test_compassionate_use_request.py` and cover:

- **Group 1 — Before Save**: `received_on` auto-stamp, `requires_verification` derivation, initial workflow state.
- **Group 2 — List Filter**: Each role's `permission_query_conditions` output, including the Team Lead fix.
- **Group 3 — Document Access**: `has_permission` for each role and workflow state combination.
- **Group 4 — Verification Guard**: Workflow transitions using `apply_workflow`; confirms the guard blocks direct Approve/Reject when verification is required and allows it after passing through the Verification state.

---

## Time Log

| Story / Task | Approx. time |
|---|---|
| Story 1 — Field set, DocType design | 3 h |
| Story 2 — Roles and permission hooks | 2 h |
| Story 3 — Workflow setup | 2 h |
| Story 4 — Verification guard | 1 h |
| Tests, type hints, mypy/Pyright config | 2 h |
| **Total** | **10 h** |

Time includes reading Frappe v16 documentation, debugging workflow action names, and resolving type-checker configuration for Frappe's untyped internals.

---

## License

MIT
