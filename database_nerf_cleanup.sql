-- SQL-запросы для очистки базы данных после нерфа экономики

-- 1. Снять все талисманы с бизнесов
UPDATE businesses SET talisman_active = 0, talisman_bonus_percent = 0, regular_talisman_active = 0;

-- 2. Удалить постоянные эффекты бизнесов
DELETE FROM permanent_business_effects;

-- 3. Удалить постоянные бонусы к призу
DELETE FROM permanent_prize_effects;

-- 4. Обнулить временные бонусы
UPDATE user_effects SET prize_bonus_percent = 0;

-- 5. Удалить все business_talisman из инвентарей
DELETE FROM inventory WHERE item_type = 'business_talisman';

-- 6. Удалить все prize_bonus из инвентарей
DELETE FROM inventory WHERE item_type = 'prize_bonus';
