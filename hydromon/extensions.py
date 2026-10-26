"""Flask extension singletons.

Kept in their own module so they can be imported without triggering circular
imports between the app factory, models and blueprints.
"""
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()
