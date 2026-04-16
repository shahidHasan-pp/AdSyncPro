"""Add name, business_name, and user_type to users table

Revision ID: 20260414_0002
Revises: 20260414_0001
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260414_0002'
down_revision = '20260414_0001'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('users', sa.Column('name', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('users', sa.Column('business_name', sa.String(length=255), nullable=True))
    user_type_enum = postgresql.ENUM('content_creator', 'business_owner', 'visitor', name='usertype')
    user_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('users', sa.Column('user_type', user_type_enum, nullable=False, server_default='visitor'))
    op.alter_column('users', 'name', server_default=None)
    op.alter_column('users', 'user_type', server_default=None)

def downgrade():
    op.drop_column('users', 'user_type')
    op.drop_column('users', 'business_name')
    op.drop_column('users', 'name')
    user_type_enum = postgresql.ENUM('content_creator', 'business_owner', 'visitor', name='usertype')
    user_type_enum.drop(op.get_bind(), checkfirst=True)
