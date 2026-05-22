# Copyright (c) 2026, Taha Shahzad and Contributors
# See license.txt

import json
from unittest.mock import patch

import frappe
from frappe import _dict
from frappe.model.document import Document
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from rmm.request_management_module.doctype.compassionate_use_request.compassionate_use_request import (
	compassionate_use_request_has_permission,
	compassionate_use_request_query,
)

EXTRA_TEST_RECORD_DEPENDENCIES: list[str] = []
IGNORE_TEST_RECORD_DEPENDENCIES: list[str] = []

_BASE_FIELDS: dict[str, object] = {
	"doctype": "Compassionate Use Request",
	"requester_name": "Dr. Test",
	"medical_license_number": "LIC-001",
	"speciality": "Oncology",
	"institution_name": "Test Hospital",
	"institution_type": "University Hospital",
	"requester_email": "test@hospital.test",
	"patient_code": "PAT-001",
	"patient_year_of_birth": 1980,
	"sex": "Male",
	"indication_code": "C50",
	"indication_description": "Breast carcinoma",
	"requested_product": "TestDrug",
	"requested_dose": "100mg daily",
	"treatment_duration": "6 months",
	"clinical_justification": "No approved alternative available.",
	"prior_treatments": "Standard chemotherapy exhausted.",
}


def make_cur(**kwargs: object) -> Document:
	"""Insert a Compassionate Use Request with sensible defaults.

	Pass field overrides as keyword arguments. Uses ignore_permissions so
	read-only / high-permlevel fields (e.g. received_on) can be set directly.
	"""
	doc: Document = frappe.get_doc({**_BASE_FIELDS, **kwargs})
	doc.insert(ignore_permissions=True)
	return doc


# ---------------------------------------------------------------------------
# Group 1 — Before Save
# ---------------------------------------------------------------------------

class TestBeforeSave(IntegrationTestCase):
	def test_new_record_workflow_state_is_new(self) -> None:
		doc = make_cur()
		self.assertEqual(doc.workflow_state, "New")

	def test_received_on_auto_set_on_creation(self) -> None:
		doc = make_cur()
		self.assertEqual(doc.received_on, nowdate())

	def test_received_on_preserved_when_already_set(self) -> None:
		doc = make_cur(received_on="2026-01-15")
		self.assertEqual(doc.received_on, "2026-01-15")

	def test_requires_verification_true_when_is_pregnancy(self) -> None:
		doc = make_cur(is_pregnancy=1)
		self.assertEqual(doc.requires_verification, 1)

	def test_requires_verification_false_when_not_pregnancy(self) -> None:
		doc = make_cur(is_pregnancy=0)
		self.assertEqual(doc.requires_verification, 0)


# ---------------------------------------------------------------------------
# Group 2 — List view filter (compassionate_use_request_query)
# ---------------------------------------------------------------------------

class TestQuery(IntegrationTestCase):
	# --- Unrestricted roles ---

	def test_administrator_sees_all(self) -> None:
		self.assertEqual(compassionate_use_request_query("Administrator"), "")

	def test_system_manager_sees_all(self) -> None:
		with patch("frappe.get_roles", return_value=["System Manager"]):
			self.assertEqual(compassionate_use_request_query("mgr@example.test"), "")

	def test_requestor_query_returns_no_sql_filter(self) -> None:
		# RMM Requestor returns "" — the "if_owner" flag on the DocType
		# permission row restricts them to their own records at the model
		# level, so no extra SQL filter is needed here.
		with patch("frappe.get_roles", return_value=["RMM Requestor"]):
			self.assertEqual(compassionate_use_request_query("req@example.test"), "")

	# --- Team Lead ---

	def test_team_lead_sees_all_records(self) -> None:
		with patch("frappe.get_roles", return_value=["RMM Team Lead"]):
			self.assertEqual(compassionate_use_request_query("lead@example.test"), "")

	# --- Assigned reviewer ---

	def test_assigned_reviewer_filtered_to_their_records(self) -> None:
		with patch("frappe.get_roles", return_value=["Some Role"]):
			result = compassionate_use_request_query("reviewer@example.test")
		self.assertIn("_assign LIKE", result)
		self.assertIn("reviewer@example.test", result)

	# --- Medical Reviewer ---

	def test_medical_reviewer_sees_allowed_states(self) -> None:
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			result = compassionate_use_request_query("medrev@example.test")
		self.assertIn("workflow_state IN", result)
		for state in ("Verification", "Approved", "Rejected"):
			self.assertIn(state, result)

	def test_medical_reviewer_also_sees_their_assigned_records(self) -> None:
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			result = compassionate_use_request_query("medrev@example.test")
		self.assertIn("_assign LIKE", result)

	def test_medical_reviewer_cannot_see_new_or_in_review_via_state_filter(self) -> None:
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			result = compassionate_use_request_query("medrev@example.test")
		self.assertNotIn("New", result)
		self.assertNotIn("In Review", result)


# ---------------------------------------------------------------------------
# Group 3 — Document-level access (compassionate_use_request_has_permission)
# ---------------------------------------------------------------------------

class TestHasPermission(IntegrationTestCase):
	def _doc(self, workflow_state: str = "New", assign: list[str] | None = None) -> _dict:
		return _dict({
			"_assign": json.dumps(assign or []),
			"workflow_state": workflow_state,
		})

	# --- Always-permitted roles ---

	def test_administrator_always_permitted(self) -> None:
		self.assertTrue(
			compassionate_use_request_has_permission(self._doc(), user="Administrator")
		)

	def test_system_manager_permitted(self) -> None:
		with patch("frappe.get_roles", return_value=["System Manager"]):
			self.assertTrue(
				compassionate_use_request_has_permission(self._doc(), user="mgr@example.test")
			)

	def test_rmm_requestor_permitted(self) -> None:
		with patch("frappe.get_roles", return_value=["RMM Requestor"]):
			self.assertTrue(
				compassionate_use_request_has_permission(self._doc(), user="req@example.test")
			)

	# --- Team Lead ---

	def test_team_lead_permitted_on_any_record(self) -> None:
		with patch("frappe.get_roles", return_value=["RMM Team Lead"]):
			self.assertTrue(
				compassionate_use_request_has_permission(self._doc(), user="lead@example.test")
			)

	# --- Assigned reviewer ---

	def test_assignee_can_access_their_record(self) -> None:
		doc = self._doc(assign=["reviewer@example.test"])
		with patch("frappe.get_roles", return_value=["Some Role"]):
			self.assertTrue(
				compassionate_use_request_has_permission(doc, user="reviewer@example.test")
			)

	def test_non_assignee_denied(self) -> None:
		doc = self._doc(assign=["someone-else@example.test"])
		with patch("frappe.get_roles", return_value=["Some Role"]):
			self.assertFalse(
				compassionate_use_request_has_permission(doc, user="nobody@example.test")
			)

	# --- Medical Reviewer ---

	def test_medical_reviewer_permitted_in_verification(self) -> None:
		doc = self._doc(workflow_state="Verification")
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			self.assertTrue(
				compassionate_use_request_has_permission(doc, user="medrev@example.test")
			)

	def test_medical_reviewer_permitted_in_approved(self) -> None:
		doc = self._doc(workflow_state="Approved")
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			self.assertTrue(
				compassionate_use_request_has_permission(doc, user="medrev@example.test")
			)

	def test_medical_reviewer_permitted_in_rejected(self) -> None:
		doc = self._doc(workflow_state="Rejected")
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			self.assertTrue(
				compassionate_use_request_has_permission(doc, user="medrev@example.test")
			)

	def test_medical_reviewer_denied_in_new(self) -> None:
		doc = self._doc(workflow_state="New")
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			self.assertFalse(
				compassionate_use_request_has_permission(doc, user="medrev@example.test")
			)

	def test_medical_reviewer_denied_in_in_review(self) -> None:
		doc = self._doc(workflow_state="In Review")
		with patch("frappe.get_roles", return_value=["RMM Medical Reviewer"]):
			self.assertFalse(
				compassionate_use_request_has_permission(doc, user="medrev@example.test")
			)

	def test_unrelated_user_denied(self) -> None:
		with patch("frappe.get_roles", return_value=["Guest"]):
			self.assertFalse(
				compassionate_use_request_has_permission(self._doc(), user="nobody@example.test")
			)


# ---------------------------------------------------------------------------
# Group 4 — Workflow verification guard
# ---------------------------------------------------------------------------

class TestWorkflowVerificationGuard(IntegrationTestCase):
	def test_cannot_approve_directly_when_verification_required(self) -> None:
		doc = make_cur(is_pregnancy=1)  # requires_verification = 1, starts at New
		apply_workflow(doc, "Start Review")  # New → In Review
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(doc, "Approve")   # In Review → Approved (blocked)

	def test_cannot_reject_directly_when_verification_required(self) -> None:
		doc = make_cur(is_pregnancy=1)
		apply_workflow(doc, "Start Review")  # New → In Review
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(doc, "Reject")    # In Review → Rejected (blocked)

	def test_can_approve_after_verification_when_required(self) -> None:
		doc = make_cur(is_pregnancy=1)
		apply_workflow(doc, "Start Review")       # New → In Review
		apply_workflow(doc, "Needs Verification") # In Review → Verification
		apply_workflow(doc, "Approve")            # Verification → Approved (allowed)
		self.assertEqual(doc.workflow_state, "Approved")

	def test_can_reject_after_verification_when_required(self) -> None:
		doc = make_cur(is_pregnancy=1)
		apply_workflow(doc, "Start Review")       # New → In Review
		apply_workflow(doc, "Needs Verification") # In Review → Verification
		apply_workflow(doc, "Reject")             # Verification → Rejected (allowed)
		self.assertEqual(doc.workflow_state, "Rejected")

	def test_can_approve_directly_when_verification_not_required(self) -> None:
		doc = make_cur(is_pregnancy=0)  # requires_verification = 0
		apply_workflow(doc, "Start Review")  # New → In Review
		apply_workflow(doc, "Approve")       # In Review → Approved (allowed)
		self.assertEqual(doc.workflow_state, "Approved")

	def test_can_reject_directly_when_verification_not_required(self) -> None:
		doc = make_cur(is_pregnancy=0)
		apply_workflow(doc, "Start Review")  # New → In Review
		apply_workflow(doc, "Reject")        # In Review → Rejected (allowed)
		self.assertEqual(doc.workflow_state, "Rejected")
