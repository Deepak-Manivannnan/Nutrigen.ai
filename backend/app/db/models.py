from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone, timedelta
from backend.app.db.session import Base

def ist_now():
    """Return current time in Indian Standard Time (UTC+5:30)"""
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    # True once the email is proven (code or Google). NULL = account created
    # before verification existed; those keep working as before.
    email_verified = Column(Boolean, nullable=True)
    # Google's permanent account id ("sub"); set when the user uses Google.
    google_id = Column(String(64), unique=True, nullable=True, index=True)
    # Nullable on purpose: registration happens before onboarding, so a user row
    # legitimately exists with no profile yet (has_profile is False until these
    # are filled in). These were NOT NULL, which made MySQL reject registration
    # on a freshly created schema with "Column 'age' cannot be null".
    age = Column(Integer, nullable=True)
    sex = Column(String(20), nullable=True)
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    activity_level = Column(String(20), nullable=True)
    goal = Column(String(20), nullable=True, default="maintain")
    diet_type = Column(String(20), nullable=True)
    dislikes = Column(JSON, default=list)
    # Persisted calorie calculation results
    bmr = Column(Float, nullable=True)
    tdee = Column(Float, nullable=True)
    min_safe_calories = Column(Float, nullable=True)
    max_safe_calories = Column(Float, nullable=True)
    suggested_target_calories = Column(Float, nullable=True)
    created_at = Column(DateTime, default=ist_now)
    updated_at = Column(DateTime, default=ist_now, onupdate=ist_now)

    allergies = relationship("UserAllergy", back_populates="user", cascade="all, delete-orphan")
    plans = relationship("Plan", back_populates="user", cascade="all, delete-orphan")

class EmailCode(Base):
    """A one-time code emailed to prove an address (sign-up or password reset).

    Only the hash of the code is stored. After a correct code, a separate
    random ticket (also stored hashed) lets the same browser finish the step.
    """
    __tablename__ = "email_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    purpose = Column(String(20), nullable=False)  # "signup" | "reset"
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    ticket_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False)


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
    # The cuisine the plan was asked for (e.g. "South Indian"); later edits stay in it. NULL for older plans.
    cuisine = Column(String(100), nullable=True)
    status = Column(String(50), default="draft")  # draft, approving, approved, rejected, escalated
    version = Column(Integer, default=1, nullable=False)
    grocery_list = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=ist_now)
    updated_at = Column(DateTime, default=ist_now, onupdate=ist_now, nullable=True)
    approved_at = Column(DateTime, nullable=True)

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

class RefreshToken(Base):
    """A long-lived "stay signed in" key (see api/routes/auth.py /refresh).

    Only a hash of the key is stored, so a database leak does not hand out
    working sessions. Each use of a key replaces it with a new one (`rotated_at`
    marks the old one); all keys from one sign-in share a `family_id` so they
    can be cancelled together. Times are naive UTC.
    """
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    family_id = Column(String(36), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    rotated_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class AgentTrace(Base):
    __tablename__ = "agent_trace"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(100), nullable=False, index=True)
    # Owner of the run, so deleting an account also deletes its trace rows.
    # NULL for rows written before this column existed.
    user_id = Column(Integer, nullable=True, index=True)
    from_agent = Column(String(50), nullable=False)
    to_agent = Column(String(50), nullable=False)
    type = Column(String(50), nullable=False)  # task, draft, feedback, veto, approve, etc.
    payload = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=ist_now, index=True)

