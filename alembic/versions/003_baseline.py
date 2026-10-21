"""baseline schema (absorbs the old bootstrap_db.py column and index changes)

Revision ID: 003_baseline
Revises: 002_analysis
Create Date: 2026-10-15 20:43:59.564558

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '003_baseline'
down_revision: Union[str, Sequence[str], None] = '002_analysis'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CATEGORY = postgresql.ENUM('AI_ML', 'WEB_DEVELOPMENT', 'Mobile', 'GAMING', 'EDUCATION', 'PRODUCTIVITY', 'OTHER',
                           name='projectcategory', create_type=False)
VISIBILITY = postgresql.ENUM('PUBLIC', 'PRIVATE', name='projectvisibility', create_type=False)


def _table(*args):
    op.create_table(*args, if_not_exists=True)


def _index(*args, **kw):
    op.create_index(*args, if_not_exists=True, **kw)


def upgrade() -> None:
    """Empty database: creates everything. Existing database: adds only what is missing."""
    for enum in (CATEGORY, VISIBILITY):
        enum.create(op.get_bind(), checkfirst=True)
    _table('techstacks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_techstacks_id'), 'techstacks', ['id'], unique=False)
    _index(op.f('ix_techstacks_name'), 'techstacks', ['name'], unique=True)
    _table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('username', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=True),
    sa.Column('github_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('username')
    )
    _table('connections',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('requester_id', sa.UUID(), nullable=False),
    sa.Column('receiver_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['receiver_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('requester_id', 'receiver_id', name='uq_requester_receiver')
    )
    _index('ix_connections_receiver', 'connections', ['receiver_id'], unique=False)
    _table('messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('sender_id', sa.UUID(), nullable=False),
    sa.Column('receiver_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['receiver_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_messages_pair_created', 'messages', ['sender_id', 'receiver_id', 'created_at'], unique=False)
    _index('ix_messages_receiver_unread', 'messages', ['receiver_id'], unique=False, postgresql_where=sa.text('read = false'))
    _table('notifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('recipient_id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('related_id', sa.String(length=255), nullable=True),
    sa.Column('message', sa.String(length=255), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recipient_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_notifications_recipient_created', 'notifications', ['recipient_id', sa.literal_column('created_at DESC')], unique=False)
    _table('password_reset_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    _index(op.f('ix_password_reset_tokens_user_id'), 'password_reset_tokens', ['user_id'], unique=False)
    _table('profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('full_name', sa.String(), nullable=True),
    sa.Column('headline', sa.String(), nullable=True),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('avatar_url', sa.String(), nullable=True),
    sa.Column('github_url', sa.String(), nullable=True),
    sa.Column('linkedin_url', sa.String(), nullable=True),
    sa.Column('website_url', sa.String(), nullable=True),
    sa.Column('skills', sa.Text(), nullable=True),
    sa.Column('location', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    _table('projects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('short_description', sa.String(), nullable=True),
    sa.Column('full_description', sa.Text(), nullable=True),
    sa.Column('category', CATEGORY, nullable=False),
    sa.Column('category_other', sa.String(length=64), nullable=True),
    sa.Column('visibility', VISIBILITY, nullable=False),
    sa.Column('cover_image_url', sa.String(), nullable=True),
    sa.Column('demo_video_url', sa.String(), nullable=True),
    sa.Column('github_url', sa.String(), nullable=True),
    sa.Column('analysis_summary', sa.Text(), nullable=True),
    sa.Column('complexity', sa.String(length=32), nullable=True),
    sa.Column('contribution_info', sa.Text(), nullable=True),
    sa.Column('language_stats', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('detected_tech_stack_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_projects_title'), 'projects', ['title'], unique=False)
    _table('collaboration_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('requester_id', sa.UUID(), nullable=False),
    sa.Column('project_owner_id', sa.UUID(), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('role', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_collab_requests_project', 'collaboration_requests', ['project_id'], unique=False)
    _table('files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('file_name', sa.String(), nullable=False),
    sa.Column('file_path', sa.String(), nullable=False),
    sa.Column('is_directory', sa.Boolean(), nullable=False),
    sa.Column('parent_path', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    _table('network_activities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('activity_type', sa.String(length=64), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('media_url', sa.String(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_activities_created', 'network_activities', [sa.literal_column('created_at DESC')], unique=False)
    _index('ix_activities_project', 'network_activities', ['project_id'], unique=False)
    _index('ix_activities_user', 'network_activities', ['user_id'], unique=False)
    _table('project_collaborators',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'user_id', name='uq_project_collaborator')
    )
    _index('ix_project_collaborators_user', 'project_collaborators', ['user_id'], unique=False)
    _table('project_likes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'user_id', name='uq_project_user_like')
    )
    _index(op.f('ix_project_likes_user_id'), 'project_likes', ['user_id'], unique=False)
    _table('project_tech',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('tech_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['tech_id'], ['techstacks.id'], ),
    sa.PrimaryKeyConstraint('project_id', 'tech_id')
    )
    _table('activity_comments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('activity_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('comment_text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['network_activities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_activity_comments_activity', 'activity_comments', ['activity_id'], unique=False)
    _table('activity_likes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('activity_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['network_activities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('activity_id', 'user_id', name='uq_activity_user_like')
    )
    _index('ix_activity_likes_user', 'activity_likes', ['user_id'], unique=False)

    # Columns and constraints that older databases received from bootstrap_db.py instead of a migration.
    for name, type_ in [
        ("category_other", sa.String(length=64)), ("github_url", sa.String()), ("analysis_summary", sa.Text()),
        ("complexity", sa.String(length=32)), ("contribution_info", sa.Text()),
        ("language_stats", postgresql.JSONB(astext_type=sa.Text())),
        ("detected_tech_stack_ids", postgresql.JSONB(astext_type=sa.Text())),
    ]:
        op.add_column("projects", sa.Column(name, type_, nullable=True), if_not_exists=True)
    op.add_column("users", sa.Column("github_id", sa.String(length=32), nullable=True), if_not_exists=True)
    op.alter_column("users", "password_hash", nullable=True)
    _index("uq_users_github_id", "users", ["github_id"], unique=True)
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_github_id_key")  # replaced by uq_users_github_id


def downgrade() -> None:
    pass  # the baseline never drops tables or data
