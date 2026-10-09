import asyncio
import httpx

async def test_full_plan_flow():
    base_url = "http://127.0.0.1:8000/api"
    async with httpx.AsyncClient(timeout=180.0) as client:
        # 1. Create a 3-day meal plan
        print("\n--- 1. Generating 3-day meal plan ---")
        chat_payload = {
            "user_id": 1,
            "message": "Plan a 3-day non-veg meal plan with 2000 calories a day",
            "task_id": "test_plan_create",
            "history": []
        }
        res = await client.post(f"{base_url}/chat", json=chat_payload)
        data = res.json()
        print("Create response status:", res.status_code)
        print("Status:", data.get("status"))
        print("Plan ID created:", data.get("plan_id"))
        print("Response:", data.get("response"))

        # 2. Check the created plan from /api/plans
        res_plans = await client.get(f"{base_url}/plans?user_id=1")
        plans = res_plans.json()
        print(f"\nTotal plans after creation: {len(plans)}")
        if not plans:
            print("ERROR: No plans found!")
            return

        plan = plans[0]
        plan_id = plan["id"]
        meals = plan.get("meals", [])
        print(f"Plan #{plan_id} has {len(meals)} meals across {plan.get('days')} days")

        # Verify no pork or beef
        pork_or_beef = [m["recipe_name"] for m in meals if any(x in (m["recipe_name"] + " " + str(m.get("ingredients", ""))).lower() for x in ["pork", "beef", "bacon", "ham"])]
        print("Pork or beef dishes found:", pork_or_beef)

        # Verify cooking instructions on meals
        meals_with_instructions = [m for m in meals if m.get("instructions")]
        print(f"Meals with cooking instructions: {len(meals_with_instructions)} / {len(meals)}")
        if meals_with_instructions:
            print("Sample recipe instructions:\n", meals_with_instructions[0].get("instructions")[:150], "...")

        # 3. Test In-Place Edit via Chat (e.g. replace Day 1 lunch)
        print(f"\n--- 2. Editing Plan #{plan_id} via Chat ---")
        edit_payload = {
            "user_id": 1,
            "message": "Change Day 1 lunch to Grilled Lemon Salmon with asparagus",
            "task_id": "test_plan_edit",
            "history": [{"role": "assistant", "content": data.get("response")}]
        }
        res_edit = await client.post(f"{base_url}/chat", json=edit_payload)
        data_edit = res_edit.json()
        print("Edit response status:", res_edit.status_code)
        print("Edit response message:", data_edit.get("response"))

        # Verify plans count in DB (MUST still be exactly the same, NO DUPLICATE DRAFT PLAN!)
        res_plans_after = await client.get(f"{base_url}/plans?user_id=1")
        plans_after = res_plans_after.json()
        print(f"\nTotal plans after edit: {len(plans_after)} (Expected: exactly {len(plans)}, no duplicate!)")
        assert len(plans_after) == len(plans), f"Duplicate plan detected! Expected {len(plans)}, got {len(plans_after)}"
        print("SUCCESS: Plan was updated in-place without duplicate plan creation!")

if __name__ == "__main__":
    asyncio.run(test_full_plan_flow())
