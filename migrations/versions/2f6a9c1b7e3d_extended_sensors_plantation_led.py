"""extended sensors, plantation date and led automation

Revision ID: 2f6a9c1b7e3d
Revises: 76ce9f34d9d8
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f6a9c1b7e3d'
down_revision = '76ce9f34d9d8'
branch_labels = None
depends_on = None


def upgrade():
    # ### readings: new sensor / actuator columns ###
    with op.batch_alter_table('readings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('temperature_outside', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('humidity_outside', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('water_temperature', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('led_on', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('led_mode', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('pump_on', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('fan_on', sa.Boolean(), nullable=True))

    # ### plantation_config: single-row plantation-date config ###
    op.create_table(
        'plantation_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plantation_date', sa.Date(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ### led_settings: single-row LED mode/schedule config ###
    op.create_table(
        'led_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('manual_state', sa.Boolean(), nullable=False),
        sa.Column('on_hour', sa.Integer(), nullable=False),
        sa.Column('off_hour', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ### invalid_payloads: audit log for rejected /predict payloads ###
    op.create_table(
        'invalid_payloads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('invalid_payloads', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_invalid_payloads_created_at'), ['created_at'], unique=False
        )


def downgrade():
    op.drop_table('invalid_payloads')
    op.drop_table('led_settings')
    op.drop_table('plantation_config')

    with op.batch_alter_table('readings', schema=None) as batch_op:
        batch_op.drop_column('fan_on')
        batch_op.drop_column('pump_on')
        batch_op.drop_column('led_mode')
        batch_op.drop_column('led_on')
        batch_op.drop_column('water_temperature')
        batch_op.drop_column('humidity_outside')
        batch_op.drop_column('temperature_outside')
