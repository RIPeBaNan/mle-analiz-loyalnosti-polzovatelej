# Импортируем библиотеки для чтения системых файлов
import os
from dotenv import load_dotenv

# Импортируем библиотеки для выгрузки и работы с датафреймами
import pandas as pd
from sqlalchemy import create_engine

# Импортируем библиотеки для расчета коэффициента корреляции
from phik import phik_matrix

class DataProcessor:
    def __init__(self, path_env = '.env'):
        # Загружаем переменные для подключения к БД из файла .env
        load_dotenv(path_env)

        self.connection_string = 'postgresql://{}:{}@{}:{}/{}'.format(os.getenv('DB_USER'), # имя пользователя
                                                         os.getenv('DB_PASSWORD'), # пароль
                                                         os.getenv('DB_HOST'),
                                                         os.getenv('DB_PORT'), # порт подключения
                                                         os.getenv('DB_NAME')) # название базы данных
        self.engine = create_engine(self.connection_string)

    # Метод, который выполняет sql-запрос и возвращает по нему датафрейм
    def load_sql_df(self, query):
        
        return pd.read_sql_query(query, self.engine)

    # Метод, который загружает csv-файл 
    def load_csv_df(self, path):
        return pd.read_csv(path)

    # Метод, который выполняет предобработку таблиц и соединяет их
    def preprocces_data(self, df_sql, df_csv):

        # Меняем тип данных в датафрейме df_sql
        df_sql['order_dt'] = df_sql['order_dt'].astype('datetime64[s]')
        df_sql['tickets_count'] = pd.to_numeric(df_sql['tickets_count'], downcast = 'integer')

        # Меняем тип данных в датафрейме df_csv
        df_csv['data'] = df_csv['data'].astype('datetime64[s]')
        df_csv['nominal'] = df_csv['nominal'].astype('int8')
        df_csv = df_csv.rename(columns = {'data': 'order_dt'})

        # Приводим к нижнему регистру и убираем лишние пробелы в столбцах event_type_main и service_name
        # для поиска неявных дубликатов
        df_sql['event_type_main'] = df_sql['event_type_main'].str.lower().str.strip()
        df_sql['service_name'] = df_sql['service_name'].str.lower().str.strip()

        # Убираем дубликаты в df_sql
        subset_cols = [col for col in df_sql.columns if col not in ['order_id']]
        df_sql = df_sql.drop_duplicates(subset = subset_cols, keep = 'first')

        # Объединяем датафрейм df_sql и df_csv
        df_sql = df_sql.merge(df_csv, on = 'order_dt', how = 'left')
        df_sql['revenue_rub'] = df_sql['revenue']
        df_sql.loc[df_sql['currency_code'] == 'kzt', 'revenue_rub'] = df_sql['revenue'] * (df_sql['curs'] / 100)
        df_sql = df_sql.drop(columns = ['nominal', 'curs', 'cdx'])

        # Фильтруем значения
        p99_revenue = df_sql['revenue_rub'].quantile(0.99)
        df_sql = df_sql[(df_sql['revenue_rub'] <= p99_revenue) &
                        (df_sql['revenue_rub'] >= 0)]

        # Возвращаем отсортированную итоговую таблицу
        return df_sql.sort_values(by = ['user_id', 'order_ts'])

    # Метод возвращает профиль пользователей
    def build_profile(self, df):
        # Получаем информацию первого заказа
        first_orders = df.drop_duplicates(subset = 'user_id', keep = 'first').set_index('user_id')

        # Рассчитываем интервалы между уникальными заказами
        unique_orders = df.drop_duplicates(subset=['user_id', 'order_id']).copy()
        unique_orders['days_diff'] = unique_orders.groupby('user_id')['order_dt'].diff().dt.days
        avg_time = unique_orders.groupby('user_id')['days_diff'].mean()

        # Агрегируем количественные метрики в новую таблицу
        profile = df.groupby('user_id').agg(
            first_order_date=('order_dt', 'min'),
            last_order_date=('order_dt', 'max'),
            total_orders=('order_id', 'nunique'),
            total_revenue=('revenue_rub', 'sum'))

        # Формируем итоговый профиль, добавляя недостающие таблицы
        profile['first_device'] = first_orders['device_type_canonical']
        profile['first_region'] = first_orders['region_name']
        profile['first_event_type'] = first_orders['event_type_main']
        profile['avg_revenue_per_order'] = profile['total_revenue'] / profile['total_orders']
        profile['avg_time_between_orders'] = avg_time

        #Добавляем признак, который показывает, чтобы было больше 1-ого заказа
        profile['is_two'] = (profile['total_orders'] >= 2).astype(int)

        # Удаляем вспомогательный столбец и выносим user_id из индекса
        profile = profile.drop(columns=['total_revenue']).reset_index()

        # Добавляем столбец с днем недели первого заказа
        profile['first_order_dow'] = profile['first_order_date'].dt.dayofweek

        # Создаем словарь для перевода дней недели и сохранения правильной сортировки
        dow_mapping = {
            0: '1_Понедельник', 
            1: '2_Вторник', 
            2: '3_Среда', 
            3: '4_Четверг', 
            4: '5_Пятница', 
            5: '6_Суббота', 
            6: '7_Воскресенье'}

        # Применяем перевод в более читаемые вид
        profile['first_order_dow'] = profile['first_order_dow'].map(dow_mapping)

        # Фильтруем таблицу, убирая все значения в total_orders превыщаующие 99-й перцентиль
        p99_orders = profile['total_orders'].quantile(0.99)
        profile = profile[profile['total_orders'] <= p99_orders]

        # Сигментируем заказы
        def categorize_orders(x):
            if x == 1:
                return '1 заказ'
            elif x == 2:
                return '2 заказа'
            elif (x >= 3) & (x <= 4):
                return 'от 3 до 4 заказов'
            return 'от 5 и выше'

        profile['cat_orders'] = profile['total_orders'].apply(categorize_orders)

        return profile

    # Функция для расчета доли возвратов (только внутри класса)
    def __get_return_stats(self, profile, feature, sort: str):
        stats = profile.groupby(feature).agg(
            users_count=('user_id', 'count'),
            return_rate=('is_two', 'mean')).reset_index()
        stats['return_rate_pct'] = stats['return_rate'] * 100
        return stats.sort_values(by = sort, ascending = False)

    # Рассчитываем доли возврата
    def calc_retention(self, profile, feature, sort: str):

        # Считаем и возвращаем метрики возврата по нужному сегменту
        return self.__get_return_stats(profile, feature, sort = sort)

    # Метод считает корреляцию по необходимым признакам и возвращает таблицу с результатами
    def calc_correlation(self, profile, columns: list, interval_cols: list = None):
        if interval_cols is None:
            interval_cols = []
        
        phik_matrix = profile[columns].phik_matrix(interval_cols = interval_cols)
        flat_phik_matrix = phik_matrix.stack().reset_index()
        flat_phik_matrix.columns = ['feature_1', 'feature_2', 'correlation']
        flat_phik_matrix = flat_phik_matrix[flat_phik_matrix['feature_1'] != flat_phik_matrix['feature_2']].sort_values(by='correlation', ascending=False).reset_index(drop=True)
        return flat_phik_matrix 