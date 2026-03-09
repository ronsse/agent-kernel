"""Tests for approval gate."""


from agent_kernel.executor.approval import ApprovalGate


class TestApprovalGate:
    """Tests for ApprovalGate."""

    def test_request_approval(self):
        """Test requesting approval."""
        gate = ApprovalGate()

        pending = gate.request_approval(
            action_id="action_123",
            capability_name="email.send@v1",
            args={"to": "test@example.com"},
            trace_id="trace_123",
            agent_profile_id="test_agent",
        )

        assert pending.action_id == "action_123"
        assert pending.capability_name == "email.send@v1"
        assert pending.status == "pending"
        assert pending.token is not None

    def test_approve(self):
        """Test approving a pending request."""
        gate = ApprovalGate()

        pending = gate.request_approval(
            action_id="action_1",
            capability_name="test@v1",
            args={},
            trace_id="trace_1",
            agent_profile_id="agent_1",
        )

        record = gate.approve(
            pending.approval_id,
            approved_by="admin",
            reason="Looks good",
        )

        assert record is not None
        assert record.approved is True
        assert record.approved_by == "admin"

        # Check status was updated
        updated = gate.get_pending(pending.approval_id)
        assert updated.status == "approved"

    def test_deny(self):
        """Test denying a pending request."""
        gate = ApprovalGate()

        pending = gate.request_approval(
            action_id="action_1",
            capability_name="dangerous@v1",
            args={},
            trace_id="trace_1",
            agent_profile_id="agent_1",
        )

        record = gate.deny(
            pending.approval_id,
            denied_by="security",
            reason="Too risky",
        )

        assert record is not None
        assert record.approved is False
        assert record.reason == "Too risky"

    def test_approve_nonexistent(self):
        """Test approving non-existent request."""
        gate = ApprovalGate()

        record = gate.approve("nonexistent")
        assert record is None

    def test_list_pending(self):
        """Test listing pending approvals."""
        gate = ApprovalGate()

        gate.request_approval("a1", "cap@v1", {}, "t1", "agent_1")
        gate.request_approval("a2", "cap@v1", {}, "t1", "agent_1")
        gate.request_approval("a3", "cap@v1", {}, "t1", "agent_2")

        pending = gate.list_pending()
        assert len(pending) == 3

        # Filter by agent
        pending = gate.list_pending(agent_profile_id="agent_1")
        assert len(pending) == 2

    def test_get_by_token(self):
        """Test getting pending approval by token."""
        gate = ApprovalGate()

        pending = gate.request_approval("a1", "cap@v1", {}, "t1", "agent_1")

        found = gate.get_by_token(pending.token)
        assert found is not None
        assert found.action_id == "a1"

    def test_validate_token(self):
        """Test token validation."""
        gate = ApprovalGate()

        pending = gate.request_approval("a1", "cap@v1", {}, "t1", "agent_1")

        # Not approved yet
        assert gate.validate_token("a1", pending.token) is False

        # Approve it
        gate.approve(pending.approval_id)

        # Now should be valid
        assert gate.validate_token("a1", pending.token) is True

    def test_cannot_approve_twice(self):
        """Test that already-processed requests cannot be re-approved."""
        gate = ApprovalGate()

        pending = gate.request_approval("a1", "cap@v1", {}, "t1", "agent_1")
        gate.approve(pending.approval_id)

        # Try to approve again
        record = gate.approve(pending.approval_id)
        assert record is None

        # Try to deny
        record = gate.deny(pending.approval_id)
        assert record is None
