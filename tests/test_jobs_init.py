import pytest


def test_jobs_module_exports_job_queries():
    from server.app.jobs import JobQueries

    assert JobQueries is not None


def test_jobs_module_lazy_loads_job_queries():
    import server.app.jobs as jobs_module

    # Trigger __getattr__ for the lazy export.
    cls = jobs_module.JobQueries
    assert cls.__name__ == "JobQueries"


def test_jobs_module_rejects_unknown_attribute():
    import server.app.jobs as jobs_module

    with pytest.raises(AttributeError, match="UnknownAttr"):
        _ = jobs_module.UnknownAttr
