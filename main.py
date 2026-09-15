import os
from platform import processor

from src.processing import DataProcessor
from pathlib import Path

def main():
    print("Инициализация обработки...\n")
    processor = DataProcessor()

    # В переменую query можно вставить любой SQL-запрос, который возвращает таблицу с нужными столбцами
    query = '''
    select p.user_id,
        p.device_type_canonical,
        p.order_id,
        p.created_dt_msk as order_dt,
        p.created_ts_msk as order_ts,
        p.currency_code,
        p.revenue,
        p.tickets_count,
	    p.created_dt_msk::date - (lag(p.created_dt_msk) over(partition by p.user_id order by p.created_dt_msk asc))::date as days_since_prev,
        p.event_id,
        e.event_name_code as event_name,
        e.event_type_main, 
        p.service_name,
        r.region_name,
        c.city_name
    from afisha.purchases p
    left join afisha.events e
        using(event_id)
    left join afisha.city c
        on e.city_id = c.city_id
    left join afisha.regions r
        on c.region_id = r.region_id
    where p.device_type_canonical in ('mobile', 'desktop') and
        e.event_type_main != 'фильм'
    order by p.user_id
    '''

    print('Загрузка данных...\n')

    # Проверяем наличие локальных файлов с данными, если их нет - загружаем из источников
    if not Path('data/df_afisha_purchases.csv').exists():
        df_sql = processor.load_sql_df(query)
        df_sql.to_csv('data/df_afisha_purchases.csv', index=False)
    else:
        df_sql = processor.load_csv_df('data/df_afisha_purchases.csv')

    if not Path('data/final_tickets_tenge_df.csv').exists():
        df_csv = processor.load_csv_df(os.getenv('CURS_CSV_PATH'))
        df_csv.to_csv('data/final_tickets_tenge_df.csv', index=False)
    else:
        df_csv = processor.load_csv_df('data/final_tickets_tenge_df.csv')

    print('Предобработка данных...\n')

    # Выполняем предобработку данных и объединяем таблицы
    df = processor.preprocces_data(df_sql, df_csv)

    print('Создание профиля пользователей...\n')

    # Создаём профиль пользователей
    profile = processor.build_profile(df)

    print('Считаю метрики возврата пользователей...\n')

    # Вычисляем метрики возврата по типу мероприятия, устройству и региону
    event_retention_stats = processor.calc_retention(profile, 'first_event_type', sort = 'return_rate')
    device_retention_stats = processor.calc_retention(profile, 'first_device', sort = 'return_rate')
    region_retention_stats = processor.calc_retention(profile, 'first_region', sort = 'users_count')

    # Находим лучшие показатели возврата по устройствам и типам мероприятий
    best_device = device_retention_stats.iloc[0]['first_device']
    best_device_rate = device_retention_stats.iloc[0]['return_rate']

    best_event = event_retention_stats.iloc[0]['first_event_type']
    best_event_rate = event_retention_stats.iloc[0]['return_rate']

    print('Вычисление корреляции...\n')

    # В переменную ниже можно поставить нужные столбцы для расчёта корреляции
    # Для примера работы вписаны столбцы из исследования
    columns = ['cat_orders', 'first_device',
               'first_region', 'first_event_type',
               'avg_revenue_per_order', 'first_order_dow']

    # Вычисляем корреляцию по выбранным признакам
    correlation = processor.calc_correlation(profile, columns, interval_cols = ['avg_revenue_per_order'])
    best_f1 = correlation.iloc[0]['feature_1']
    best_f2 = correlation.iloc[0]['feature_2']
    best_corr = correlation.iloc[0]['correlation']

    # Вычисляем корреляцию с признаком "cat_orders" и находим наибольшую
    cat_orders = correlation[correlation['feature_1'] == 'cat_orders'].sort_values(by = 'correlation', ascending = False)
    best_cat_orders = cat_orders.iloc[0]['feature_2']
    best_corr_cat_orders = correlation[correlation['feature_1'] == 'cat_orders'].iloc[0]['correlation']
    
    print('\n\n\tОтчёт по анализу данных\t\n')
    print(f"1. Всего уникальных пользователей в отфильтрованной выборке: {len(profile)}")
    print(f"2. Средний процент возврата пользователя в сервис составляет {profile['is_two'].mean():.2%}")

    print("3. Топ-3 региона по возвращаемости:")
    # Выводим топ-3 региона по возвращаемости
    for row in range(3):
        print(f'\t{row + 1}. {region_retention_stats.iloc[row]["first_region"]} - {region_retention_stats.iloc[row]["return_rate"]:.2%} ({region_retention_stats.iloc[row]["users_count"]} пользователей)')
        row += 1

    print(f"4. Пользователи, совершившие первый заказ на устройстве {best_device} возвращаются чаще всего - {best_device_rate:.2%} ({device_retention_stats.iloc[0]['users_count']} пользователей)")
    print(f'5. Пользователи, совершившие первый заказ на мероприятия типа {best_event} возвращаются чаще всего - {best_event_rate:.2%} ({event_retention_stats.iloc[0]["users_count"]} пользователей)')
    print(f'6. Наибольшую кореляцию с возвратом пользователей имеет пара признаков "{best_f1} - {best_f2}" - {best_corr:.2f}')
    print(f'7. Наибольшую корреляцию с признаком "число заказов" имеет признак "{best_cat_orders}" - {best_corr_cat_orders:.2f}')

if __name__ == "__main__":
    main()