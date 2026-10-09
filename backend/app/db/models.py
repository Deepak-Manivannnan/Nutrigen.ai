from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.app.db.session import Base

def utc_now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    age = Column(Integer, nullable=False)
    sex = Column(String(20), nullable=False)
    height_cm = Column(Float, nullable=False)
    weight_kg = Column(Float, nullable=False)
    activity_level = Column(String(20), nullable=False)
    goal = Column(String(20), nullable=True, default="maintain")
    diet_type = Column(String(20), nullable=False)
    dislikes = Column(JSON, default=list)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    allergies = relationship("UserAllergy", back_populates="user", cascade="all, delete-orphan")
    plans = relationship("Plan", back_populates="user", cascade="all, delete-orphan")

class UserAllergy(Base):
    __tablename__ = "user_allergies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    allergen = Column(String(100), nullable=False, index=True)

    user = relationship("User", back_populates="allergies")


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(150), nullable=True)
    days = Column(Integer, nullable=False, default=7)
    calorie_target = Column(Float, nullable=False)
    diet_type = Column(String(50), default="veg", nullable=True)
    status = Column(String(50), default="draft")  # draft, approved, rejected, escalated
    version = Column(Integer, default=1, nullable=False)
    grocery_list = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="plans")
    meals = relationship("PlanMeal", back_populates="plan", cascade="all, delete-orphan")

class PlanMeal(Base):
    __tablename__ = "plan_meals"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(Integer, nullable=False)
    meal_type = Column(String(50), nullable=False)
    locked = Column(Boolean, default=False)
    diet_type = Column(String(50), default="veg", nullable=True)
    recipe_name = Column(String(200), nullable=False)
    source = Column(String(50), default="api")  # api, ai_generated, user_specified
    ingredients = Column(JSON, default=list)  # list of {name, amount_grams, notes}
    calories = Column(Float, default=0.0)
    instructions = Column(Text, nullable=True)
    macros = Column(JSON, default=dict)  # {protein_g, carbs_g, fat_g}

    plan = relationship("Plan", back_populates="meals")

class AgentTrace(Base):
    __tablename__ = "agent_trace"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(100), nullable=False, index=True)
    from_agent = Column(String(50), nullable=False)
    to_agent = Column(String(50), nullable=False)
    type = Column(String(50), nullable=False)  # task, draft, feedback, veto, approve, etc.
    payload = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=utc_now, index=True)

