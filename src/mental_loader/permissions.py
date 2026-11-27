"""Shared access rules for browser, worker, deterministic tools and push delivery."""

from sqlalchemy import and_, exists, or_, select

from mental_loader.storage import Conversation, ConversationParticipant, Membership


def visible_conversations(household_id, user_id=None):
    participant = exists(select(ConversationParticipant.user_id).where(
        ConversationParticipant.conversation_id == Conversation.id,
        ConversationParticipant.household_id == household_id,
        ConversationParticipant.user_id == user_id,
    )) if user_id else False
    return and_(Conversation.household_id == household_id, or_(
        Conversation.visibility == "household", participant,
    ))


def can_access(db, household_id, conversation_id, user_id=None):
    if user_id and db.get(Membership, (household_id, user_id)) is None:
        return False
    return db.scalar(select(Conversation.id).where(
        Conversation.id == conversation_id, visible_conversations(household_id, user_id)
    )) is not None
