"""Tests for task management capabilities."""

import pytest

from agent_kernel.tools.builtin.tasks import (
    TaskStore,
    clear_tasks,
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    search_tasks,
    set_task_store,
    update_task,
)


@pytest.fixture
def task_store():
    """Create a fresh in-memory task store."""
    store = TaskStore(":memory:")
    set_task_store(store)
    yield store
    clear_tasks()


class TestTaskStore:
    """Tests for TaskStore class."""

    def test_create_task(self, task_store):
        """Test creating a task."""
        task = task_store.create(
            title="Test task",
            description="A test description",
            priority="high",
        )

        assert task.task_id is not None
        assert task.title == "Test task"
        assert task.description == "A test description"
        assert task.priority == "high"
        assert task.status == "open"

    def test_get_task(self, task_store):
        """Test getting a task."""
        created = task_store.create(title="Test")
        retrieved = task_store.get(created.task_id)

        assert retrieved is not None
        assert retrieved.task_id == created.task_id
        assert retrieved.title == "Test"

    def test_get_nonexistent(self, task_store):
        """Test getting nonexistent task."""
        result = task_store.get("nonexistent")
        assert result is None

    def test_update_task(self, task_store):
        """Test updating a task."""
        task = task_store.create(title="Original")
        updated = task_store.update(
            task.task_id,
            title="Updated",
            priority="high",
        )

        assert updated is not None
        assert updated.title == "Updated"
        assert updated.priority == "high"

    def test_complete_task(self, task_store):
        """Test completing a task."""
        task = task_store.create(title="To complete")
        completed = task_store.complete(task.task_id)

        assert completed is not None
        assert completed.status == "completed"
        assert completed.completed_at is not None

    def test_reopen_task(self, task_store):
        """Test reopening a task."""
        task = task_store.create(title="To reopen")
        task_store.complete(task.task_id)
        reopened = task_store.reopen(task.task_id)

        assert reopened is not None
        assert reopened.status == "open"
        assert reopened.completed_at is None

    def test_delete_task(self, task_store):
        """Test deleting a task."""
        task = task_store.create(title="To delete")
        deleted = task_store.delete(task.task_id)

        assert deleted is True
        assert task_store.get(task.task_id) is None

    def test_delete_nonexistent(self, task_store):
        """Test deleting nonexistent task."""
        deleted = task_store.delete("nonexistent")
        assert deleted is False

    def test_list_tasks(self, task_store):
        """Test listing tasks."""
        task_store.create(title="Task 1")
        task_store.create(title="Task 2")
        task_store.create(title="Task 3")

        tasks, count = task_store.list()

        assert count == 3
        assert len(tasks) == 3

    def test_list_tasks_with_status_filter(self, task_store):
        """Test listing tasks with status filter."""
        task1 = task_store.create(title="Open task")
        task_store.create(title="Another open")
        task_store.complete(task1.task_id)

        open_tasks, open_count = task_store.list(status="open")
        completed_tasks, completed_count = task_store.list(status="completed")

        assert open_count == 1
        assert completed_count == 1

    def test_list_tasks_with_project_filter(self, task_store):
        """Test listing tasks with project filter."""
        task_store.create(title="Project A task", project_id="proj-a")
        task_store.create(title="Project B task", project_id="proj-b")
        task_store.create(title="No project")

        tasks, count = task_store.list(project_id="proj-a")

        assert count == 1
        assert tasks[0].project_id == "proj-a"

    def test_list_tasks_with_limit(self, task_store):
        """Test listing tasks with limit."""
        for i in range(10):
            task_store.create(title=f"Task {i}")

        tasks, count = task_store.list(limit=5)

        assert count == 10  # Total count
        assert len(tasks) == 5  # Limited results

    def test_search_tasks(self, task_store):
        """Test searching tasks."""
        task_store.create(title="Buy groceries")
        task_store.create(title="Buy books", description="Programming books")
        task_store.create(title="Walk the dog")

        results = task_store.search("buy")

        assert len(results) == 2

    def test_search_in_description(self, task_store):
        """Test searching in description."""
        task_store.create(
            title="Learn Python",
            description="Practice programming every day",
        )
        task_store.create(title="Other task")

        results = task_store.search("programming")

        assert len(results) == 1
        assert results[0].title == "Learn Python"

    def test_task_with_tags(self, task_store):
        """Test task with tags."""
        task = task_store.create(
            title="Tagged task",
            tags=["work", "important"],
        )

        retrieved = task_store.get(task.task_id)

        assert retrieved is not None
        assert "work" in retrieved.tags
        assert "important" in retrieved.tags


class TestTaskFunctions:
    """Tests for task capability functions."""

    def test_list_tasks_function(self, task_store):
        """Test list_tasks function."""
        task_store.create(title="Task 1")
        task_store.create(title="Task 2")

        result = list_tasks()

        assert "tasks" in result
        assert "total_count" in result
        assert result["total_count"] == 2

    def test_create_task_function(self, task_store):
        """Test create_task function."""
        result = create_task(
            title="New task",
            description="Description",
            priority="high",
        )

        assert "task_id" in result
        assert "created_at" in result
        assert result["title"] == "New task"

    def test_get_task_function(self, task_store):
        """Test get_task function."""
        created = create_task(title="Test")
        result = get_task(created["task_id"])

        assert "task" in result
        assert result["task"]["title"] == "Test"

    def test_get_task_not_found(self, task_store):
        """Test get_task with invalid ID."""
        result = get_task("invalid")

        assert "error" in result

    def test_update_task_function(self, task_store):
        """Test update_task function."""
        created = create_task(title="Original")
        result = update_task(created["task_id"], title="Updated")

        assert "task" in result
        assert result["task"]["title"] == "Updated"

    def test_complete_task_function(self, task_store):
        """Test complete_task function."""
        created = create_task(title="To complete")
        result = complete_task(created["task_id"])

        assert "task" in result
        assert result["task"]["status"] == "completed"

    def test_delete_task_function(self, task_store):
        """Test delete_task function."""
        created = create_task(title="To delete")
        result = delete_task(created["task_id"])

        assert result["deleted"] is True

        # Verify it's gone
        get_result = get_task(created["task_id"])
        assert "error" in get_result

    def test_search_tasks_function(self, task_store):
        """Test search_tasks function."""
        create_task(title="Buy groceries")
        create_task(title="Walk dog")

        result = search_tasks("groceries")

        assert result["total_count"] == 1
        assert len(result["tasks"]) == 1
