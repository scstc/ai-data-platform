from app.models import DatasetVersionTable


def test_dvt_table_and_constraints():
    t = DatasetVersionTable.__table__
    assert t.name == "dataset_version_tables"
    cols = set(t.columns.keys())
    assert {
        "id", "dataset_version_id", "table_name", "storage_uri",
        "format", "rows", "size", "schema_snapshot", "schema_variant",
        "created_at",
    } <= cols
    cons = {c.name for c in t.constraints if c.name}
    assert "uq_dvt_version_table" in cons
    idx = {i.name for i in t.indexes}
    assert "ix_dvt_version" in idx
    # 弱关联约定:无外键
    assert not t.foreign_keys
