def test_delete_app_removes_everything_but_spend(tmp_db, tmp_path, monkeypatch):
    from engine import settings

    db = tmp_db
    for name in ("DATA_DIR", "OUTPUTS_DIR", "CACHE_DIR"):
        monkeypatch.setattr(settings, name, tmp_path / name.lower())
    files = settings.DATA_DIR / "apps" / "gone"
    files.mkdir(parents=True)
    (files / "screenshot_0.png").write_bytes(b"x")
    with db.session() as s:
        for app_id in ("gone", "kept"):
            s.add(db.App(id=app_id, name=app_id, category="c", brief={}))
        s.add(db.Persona(app_id="gone", key="p", name="P", role="presenter"))
        s.add(db.Batch(app_id="gone"))
        s.add(db.Ledger(app_id="gone", step="magnific:image", provider="magnific", cost_inr=0))
        s.commit()
        run = db.ProductionRun(app_id="gone", creative_id=1)
        s.add(run)
        s.commit()
        s.add(db.ReviewTask(run_id=run.id, version=1, raised_by="qc", assigned_to="editor", issue="x"))
        s.commit()

    db.delete_app("gone")

    from sqlmodel import select
    with db.session() as s:
        assert s.get(db.App, "gone") is None and s.get(db.App, "kept") is not None
        for model in (db.Persona, db.Batch, db.ProductionRun):
            assert not s.exec(select(model).where(model.app_id == "gone")).all()
        assert not s.exec(select(db.ReviewTask)).all()
        assert len(s.exec(select(db.Ledger).where(db.Ledger.app_id == "gone")).all()) == 1   # spend history kept
    assert not files.exists()
