// Mock agent workflow simulation for demo purposes
// When backend is connected, this will be replaced with real SSE events

export function simulateWorkflow(setWorkflowState, setChatStatus, setIsAgentRunning, addMessage, addPlan, setGrocery, navigateTo, userMessage) {
  const isGroceryRequest = /approv|okay|looks good|proceed|grocery|shopping/i.test(userMessage);

  if (isGroceryRequest) {
    return simulateGroceryWorkflow(setWorkflowState, setChatStatus, setIsAgentRunning, addMessage, setGrocery, navigateTo, userMessage);
  }
  return simulatePlanWorkflow(setWorkflowState, setChatStatus, setIsAgentRunning, addMessage, addPlan, navigateTo, userMessage);
}

function simulatePlanWorkflow(setWorkflowState, setChatStatus, setIsAgentRunning, addMessage, addPlan, navigateTo, userMessage) {
  setIsAgentRunning(true);

  const initState = {
    request: userMessage,
    phase: 'planning',
    agents: {
      supervisor: 'running',
      planner: 'pending',
      nutrition: 'pending',
      allergy: 'pending',
      shopper: 'pending',
    },
    revisionRound: null,
    done: false,
  };
  setWorkflowState(initState);
  setChatStatus('Analyzing your request...');

  // Supervisor
  setTimeout(() => {
    setWorkflowState(prev => ({ ...prev, agents: { ...prev.agents, supervisor: 'completed', planner: 'running' } }));
    setChatStatus('Creating your meal plan...');
  }, 1200);

  // Planner
  setTimeout(() => {
    setWorkflowState(prev => ({ ...prev, agents: { ...prev.agents, planner: 'completed', nutrition: 'running', allergy: 'running' } }));
    setChatStatus('Checking nutrition and allergies...');
  }, 3500);

  // Parallel checks
  setTimeout(() => {
    setWorkflowState(prev => ({ ...prev, agents: { ...prev.agents, nutrition: 'completed', allergy: 'completed' } }));
    setChatStatus('Finalizing your meal plan...');
  }, 5500);

  // Supervisor final
  setTimeout(() => {
    setWorkflowState(prev => ({ ...prev, agents: { ...prev.agents }, done: true, phase: 'complete' }));
    setChatStatus(null);
    setIsAgentRunning(false);

    // Generate a demo plan
    const plan = generateDemoPlan(userMessage);
    addPlan(plan);

    addMessage({
      role: 'assistant',
      content: 'Your meal plan has been created successfully.',
      actionType: 'view_plan',
      actionLabel: 'View Meal Plan →',
      actionTarget: 'plans',
      planId: plan.id,
    });
  }, 7000);
}

function simulateGroceryWorkflow(setWorkflowState, setChatStatus, setIsAgentRunning, addMessage, setGrocery, navigateTo, userMessage) {
  setIsAgentRunning(true);

  setWorkflowState(prev => ({
    ...prev,
    phase: 'grocery',
    agents: {
      ...((prev && prev.agents) || {}),
      supervisor: 'completed',
      planner: 'completed',
      nutrition: 'completed',
      allergy: 'completed',
      shopper: 'running',
    },
    done: false,
  }));
  setChatStatus('Preparing your grocery list...');

  setTimeout(() => {
    setChatStatus('Calculating ingredients and estimated cost...');
  }, 2000);

  setTimeout(() => {
    setWorkflowState(prev => ({ ...prev, agents: { ...prev.agents, shopper: 'completed' }, done: true, phase: 'grocery_complete' }));
    setChatStatus(null);
    setIsAgentRunning(false);

    const grocery = generateDemoGrocery();
    setGrocery(grocery);

    addMessage({
      role: 'assistant',
      content: 'Your grocery list and estimated cost are ready. I\'ve prepared the ingredients and quantities based on your approved meal plan.',
      actionType: 'view_grocery',
      actionLabel: 'View Grocery List →',
      actionTarget: 'grocery',
    });
  }, 4500);
}

export function generateDemoPlan(userMessage = '7-day 1800 cal veg') {
  const daysMatch = userMessage.match(/(\d+)[\s-]*day/i);
  const calMatch = userMessage.match(/(\d[,\d]*)\s*(cal|kcal)/i);
  const days = daysMatch ? parseInt(daysMatch[1]) : 7;
  const calories = calMatch ? parseInt(calMatch[1].replace(',', '')) : 1800;
  const isVeg = /veg/i.test(userMessage);

  const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const mealTemplates = {
    veg: {
      breakfast: ['Oatmeal with berries', 'Vegetable upma', 'Whole grain toast with avocado', 'Yogurt parfait', 'Poha', 'Idli with sambar', 'Paneer paratha'],
      lunch: ['Dal rice with sabzi', 'Vegetable biryani', 'Paneer wrap', 'Rajma chawal', 'Mixed veg pulao', 'Chole roti', 'Tofu stir-fry with rice'],
      dinner: ['Palak paneer with roti', 'Lentil soup & salad', 'Veg curry with brown rice', 'Paneer tikka with naan', 'Dal khichdi', 'Mixed veg sabzi with chapati', 'Sprouts salad & soup'],
      snack: ['Mixed nuts & berries', 'Roasted makhana', 'Fruit bowl', 'Hummus with carrots', 'Sprouts chaat', 'Buttermilk & almonds', 'Greek yogurt cup'],
    },
    nonveg: {
      breakfast: ['Eggs & whole grain toast', 'Chicken omelette', 'Boiled eggs with fruit', 'Egg bhurji with toast', 'Greek yogurt & granola', 'Poached eggs on toast', 'Egg paratha'],
      lunch: ['Grilled chicken with salad', 'Chicken rice bowl', 'Fish curry with rice', 'Chicken wrap', 'Egg fried rice', 'Chicken biryani', 'Tuna salad sandwich'],
      dinner: ['Grilled fish with veggies', 'Chicken curry with roti', 'Baked salmon & quinoa', 'Mutton stew', 'Chicken stir-fry', 'Prawn masala with rice', 'Chicken soup & bread'],
      snack: ['Boiled eggs with pepper', 'Protein shake', 'Peanut butter toast', 'Mixed trail nuts', 'Cottage cheese & apple', 'Chicken skewers', 'Fruit & almonds'],
    },
  };
  const meals = isVeg ? mealTemplates.veg : mealTemplates.nonveg;

  const planDays = Array.from({ length: days }, (_, i) => ({
    day: i + 1,
    label: dayNames[i % 7],
    meals: [
      {
        type: 'Breakfast',
        name: meals.breakfast[i % meals.breakfast.length],
        calories: Math.round(calories * 0.25),
        protein: 18,
        carbs: 42,
        fat: 10,
        ingredients: ['Fresh ingredients', 'Whole grains', 'Spices & olive oil'],
      },
      {
        type: 'Lunch',
        name: meals.lunch[i % meals.lunch.length],
        calories: Math.round(calories * 0.35),
        protein: 28,
        carbs: 58,
        fat: 14,
        ingredients: ['Seasonal greens', 'Legumes/dal', 'Brown rice or chapati'],
      },
      {
        type: 'Dinner',
        name: meals.dinner[i % meals.dinner.length],
        calories: Math.round(calories * 0.28),
        protein: 24,
        carbs: 46,
        fat: 12,
        ingredients: ['Steamed vegetables', 'Paneer/Lentils', 'Herbs & spices'],
      },
      {
        type: 'Snack',
        name: (meals.snack || ['Mixed nuts & fruit'])[i % (meals.snack || []).length],
        calories: Math.round(calories * 0.12),
        protein: 6,
        carbs: 16,
        fat: 8,
        ingredients: ['Almonds, walnuts, seasonal fruit'],
      },
    ],
    totalCalories: calories,
  }));

  return {
    id: Date.now(),
    name: `${days}-Day ${isVeg ? 'Vegetarian' : 'Balanced'} Plan`,
    days,
    calorieTarget: calories,
    dietType: isVeg ? 'Vegetarian' : 'Non-Vegetarian',
    status: 'draft',
    createdAt: new Date().toISOString(),
    planDays,
    nutritionPassed: true,
    allergyPassed: true,
    version: 1,
  };
}

export function generateDemoGrocery() {
  const categories = {
    'Vegetables': [
      { name: 'Spinach', qty: '500 g', price: 30, source: 'mandi_api' },
      { name: 'Tomatoes', qty: '500 g', price: 30, source: 'mandi_api' },
      { name: 'Onions', qty: '1 kg', price: 35, source: 'mandi_api' },
      { name: 'Broccoli', qty: '300 g', price: 45, source: 'ai_estimate' },
      { name: 'Bell Peppers', qty: '400 g', price: 60, source: 'ai_estimate' },
    ],
    'Grains': [
      { name: 'Brown Rice', qty: '1 kg', price: 80, source: 'baseline_cache' },
      { name: 'Whole Wheat Roti', qty: '1 pack (500g)', price: 50, source: 'baseline_cache' },
      { name: 'Oats', qty: '500 g', price: 70, source: 'mandi_api' },
    ],
    'Protein': [
      { name: 'Eggs', qty: '12 pieces', price: 80, source: 'baseline_cache' },
      { name: 'Chicken Breast', qty: '500 g', price: 200, source: 'ai_estimate' },
      { name: 'Lentils (Dal)', qty: '500 g', price: 65, source: 'baseline_cache' },
      { name: 'Paneer', qty: '250 g', price: 90, source: 'baseline_cache' },
    ],
    'Dairy': [
      { name: 'Milk', qty: '2 L', price: 100, source: 'baseline_cache' },
      { name: 'Yogurt (Curd)', qty: '500 g', price: 45, source: 'baseline_cache' },
    ],
    'Fruits': [
      { name: 'Bananas', qty: '6 pieces', price: 40, source: 'mandi_api' },
      { name: 'Apples', qty: '4 pieces', price: 80, source: 'mandi_api' },
      { name: 'Mixed Berries', qty: '200 g', price: 120, source: 'ai_estimate' },
    ],
    'Other': [
      { name: 'Olive Oil', qty: '250 ml', price: 180, source: 'baseline_cache' },
      { name: 'Mixed Nuts', qty: '200 g', price: 150, source: 'ai_estimate' },
    ],
  };

  const total = Object.values(categories).flat().reduce((s, i) => s + i.price, 0);
  return { categories, total, duration: 7, currency: '₹' };
}

