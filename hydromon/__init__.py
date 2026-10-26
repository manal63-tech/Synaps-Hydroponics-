"""Hydromon — hydroponic monitoring application package.

Exposes an application factory (:func:`create_app`) that wires together
configuration, the database, migrations and the route blueprints.
"""
from flask import Flask

from .config import Config
from .extensions import db, migrate


def create_app(config_class: type[Config] = Config) -> Flask:
    """Application factory.

    Optional data-analysis modules stay out of application startup so
    lightweight tooling such as ``flask db`` can import the app directly.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)
    from .rule_config import load_rule_metadata

    app.extensions["hydromon_rule_metadata"] = load_rule_metadata()

    # Initialise extensions.
    db.init_app(app)
    migrate.init_app(app, db)

    # Import models so that they are registered with SQLAlchemy / Alembic.
    from . import db_models  # noqa: F401

    with app.app_context():
        db.create_all()
        from .plantation import get_plantation_config

        get_plantation_config()

    # Register blueprints.
    from .routes.dashboard import dashboard_bp
    from .routes.api import api_bp
    from .services import start_export_scheduler

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)

    if app.config.get("ENABLE_EXPORT_SCHEDULER", True):
        start_export_scheduler(app)

    return app
