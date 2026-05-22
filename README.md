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

The field set is designed to mirror the real-world flow of a compassionate use request — three parties are involved, and the form captures what each party contributes:

1. **Patient (third party)** — the patient is never a system user, so they are identified by an anonymised code and initials rather than a name. Year of birth and sex give reviewers enough clinical context without storing unnecessary personal data.
2. **Requestor (external physician)** — name, licence number, specialty, and institution tell the review team who is asking and whether they are qualified. The requestor submits the form but is not an internal employee.
3. **Review team (internal)** — the clinical criteria flags (off-label, paediatric, pregnancy) are checkboxes the doctor fills in that flag the request for closer review. The decision section (rationale, date, BfArM programme number) is filled in by the review team after their decision is made.

`prior_treatments` is a required field because "no approved alternative exists" is the primary qualifying criterion for compassionate use. `indication_code + indication_description` lets reviewers reference a standardised code while still reading a plain-language explanation.

**A note on field completeness:** This field set is a best-effort approximation of what a real compassionate use request form might contain. I am not a domain expert in pharmaceutical regulation. Given a proper clinical or regulatory brief, the field set would be revised to match the exact requirements of the applicable programme (e.g. BfArM §21 AMG in Germany).

### Who can read and edit which fields

Field-level access is controlled using Frappe's `permlevel` system, which assigns a numeric level to each field and a matching level to each role's permission row. A role can only read or write fields at or below its permitted level.

| Permlevel | Fields | Who can write |
|---|---|---|
| **3** | All doctor-facing fields (requester details, patient details, clinical request, clinical criteria) | RMM Requestor (the doctor) |
| **4** | Decision fields (decision date, rationale, BfArM programme number) | RMM Agent, RMM Medical Reviewer |
| **1** | Naming series, `requires_verification` (read-only) | System / auto-set |

The doctor can read the decision fields once they are filled in, but cannot edit them. The review team can read everything the doctor entered but cannot modify it after submission. This separation is enforced at the model level by Frappe — no custom code is needed.

### Roles

| Role | Create | Read | Write |
|---|---|---|---|
| **RMM Requestor** | Yes — own requests only | Own requests only | Doctor-facing fields (permlevel 3) |
| **RMM Team Lead** | No | All requests | All requests; assigns reviewers |
| **RMM Agent** | No | Requests assigned to them | All fields including decision (permlevel 4) |
| **RMM Medical Reviewer** | No | Requests in Verification, Approved, Rejected (plus any assigned to them) | Decision fields (permlevel 4) |

**How visibility is enforced:**

- **RMM Requestor** — Frappe's built-in `if_owner` flag on the DocType permission row restricts them to records they created. No custom code needed.
- **RMM Team Lead** — the list-filter hook returns no SQL condition for this role, so they see all records.
- **RMM Agent** — the list-filter hook restricts them to records where their email appears in the assignment list (`_assign`). They only see what has been assigned to them.
- **RMM Medical Reviewer** — the list-filter hook adds a `workflow_state IN ('Verification', 'Approved', 'Rejected')` condition so they only see records that have reached those states. If a record is explicitly assigned to them, they can also see it regardless of state. A document-level hook enforces the same rules when a record is opened directly.

---

## Story 2 — Workflow

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

Once a doctor submits a request it starts in **New**. An agent picks it up and moves it to **In Review**. From there the agent has four options:

- **Approve or Reject** directly — allowed when the request does not require medical verification.
- **Needs Verification** — escalates the request to a Medical Reviewer. The Medical Reviewer can then approve or reject it from the **Verification** state.
- **Needs Correction** — sends the request back to the doctor. It enters **Awaiting Requestor** and the doctor can update and resubmit it, which returns it to **In Review**.

Each transition is restricted to the correct role inside the Frappe Workflow definition. Frappe only shows the buttons a user is allowed to click — no custom code is needed to hide or block the wrong transitions for the wrong role.

### The Verification step and how it is enforced

The **Verification** state is always present in the workflow. When a doctor checks `is_pregnancy` on a request, the system automatically sets `requires_verification = 1`. This marks the request as one that must go through the Verification state before a final decision is made.

There are two layers of enforcement so this rule cannot be bypassed:

1. **UI layer** — because each workflow transition is tied to a specific role, an agent reviewing a pregnancy request will only see "Needs Verification" as their escalation option; the "Approve" and "Reject" buttons that skip Verification are not presented to them.

2. **Backend guard** — even if someone bypasses the UI and calls the API directly, a check in the document's `validate()` method catches it. If the request is being moved to Approved or Rejected, `requires_verification` is `1`, and the last saved state in the database was not `Verification`, Frappe raises a validation error and rejects the save. This makes the rule impossible to bypass from any direction.

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

`requires_verification` is set automatically from `is_pregnancy` when the record is saved and is read-only, so neither the doctor nor a reviewer can override it manually.

---

## Stories Completed / Skipped

| Story | Status | Notes |
|---|---|---|
| Story 1 — Field set, DocType, and roles | Complete | All fields, permlevels, naming series, and role-based access configured |
| Story 2 — Workflow | Complete | Six states, eight transitions, role-restricted; Verification enforced at UI and backend |
| Story 3 — Verification stage | Complete via Frappe core | Built into the workflow; `validate()` guard closes the backend bypass |

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
| Story 1 — Field set, DocType, roles and permissions | 3 h |
| Story 2 — Workflow setup | 2 h |
| Story 3 — Verification guard | 1 h |
| Tests, type hints, mypy/Pyright config | 2 h |
| **Total** | **8 h** |

Time includes reading Frappe v16 documentation, debugging workflow action names, and resolving type-checker configuration for Frappe's untyped internals.

---

## License

MIT
