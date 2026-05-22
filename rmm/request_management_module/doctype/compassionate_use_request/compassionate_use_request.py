# Copyright (c) 2026, Taha Shahzad and contributors
# For license information, please see license.txt

# import frappe
import frappe
from frappe.model.document import Document


class CompassionateUseRequest(Document):
	received_on: str | None

	def validate(self) -> None:
		if (
			self.workflow_state in ("Approved", "Rejected")
			and self.requires_verification
			and (self.get_db_value("workflow_state") or "New") != "Verification"
		):
			frappe.throw(
				"This request requires medical verification before it can be approved or rejected."
			)

	def before_save(self) -> None:
		if not self.received_on:
			self.received_on = frappe.utils.nowdate()

		self.requires_verification = self.is_pregnancy
	
REVIEWER_ALLOWED_STATES: tuple[str, ...] = ("Verification", "Approved", "Rejected")

def compassionate_use_request_query(user: str) -> str:
    if not user:
        user = frappe.session.user or ""

    roles = frappe.get_roles(user)

    if user == "Administrator" or "System Manager" in roles or "RMM Requestor" in roles or "RMM Team Lead" in roles:
        return ""

    safe_user = str(user)
    conditions = [
        f"`tabCompassionate Use Request`._assign LIKE {frappe.db.escape('%' + safe_user + '%')}"
    ]

    if "RMM Medical Reviewer" in roles:
        states = ", ".join(frappe.db.escape(s) for s in REVIEWER_ALLOWED_STATES)
        conditions.append(
            f"`tabCompassionate Use Request`.workflow_state IN ({states})"
        )

    return "(" + " OR ".join(conditions) + ")"


def compassionate_use_request_has_permission(
    doc: Document,
    user: str | None = None,
    _permission_type: str | None = None,
) -> bool:
    if not user:
        user = frappe.session.user

    roles = frappe.get_roles(user)

    if user == "Administrator" or "System Manager" in roles or "RMM Requestor" in roles or "RMM Team Lead" in roles:
        return True

    assignees = frappe.parse_json(str(doc.get("_assign") or "[]"))
    if user and user in assignees:
        return True

    if (
        "RMM Medical Reviewer" in roles
        and doc.get("workflow_state") in REVIEWER_ALLOWED_STATES
    ):
        return True

    return False
