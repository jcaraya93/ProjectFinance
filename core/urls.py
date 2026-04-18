from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('spending-income/', views.spending_income_dashboard, name='spending_income_dashboard'),
    path('chart-comparison/', views.chart_comparison, name='chart_comparison'),
    path('car/', views.car_dashboard, name='car_dashboard'),
    path('car/gas/', views.car_gas_dashboard, name='car_gas_dashboard'),
    path('car/parking/', views.car_parking_dashboard, name='car_parking_dashboard'),
    path('income/salary/', views.income_salary_dashboard, name='income_salary_dashboard'),
    path('transaction-health/', views.transaction_health_dashboard, name='transaction_health_dashboard'),
    path('rule-matching/', views.rule_matching_dashboard, name='rule_matching_dashboard'),
    path('default-buckets/', views.default_buckets_dashboard, name='default_buckets_dashboard'),
    path('upload/', views.upload, name='upload'),
    path('upload/file/', views.upload_file_api, name='upload_file_api'),
    path('statements/', views.statement_list, name='statement_list'),
    path('statements/purge/', views.purge_all_data, name='purge_all_data'),
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('transactions/bulk-update-category/', views.bulk_update_category, name='bulk_update_category'),
    path('transactions/<int:raw_id>/edit/', views.edit_transaction, name='edit_transaction'),
    path('transactions/<int:raw_id>/split/', views.split_transaction, name='split_transaction'),
    path('transactions/<int:raw_id>/unsplit/', views.unsplit_transaction, name='unsplit_transaction'),
    # Categories management
    path('categories/', views.category_list, name='category_list'),
    path('categories/export/', views.export_categories, name='export_categories'),
    path('categories/import/', views.import_categories, name='import_categories'),
    path('categories/add/', views.yaml_category_add, name='yaml_category_add'),
    path('categories/delete/', views.yaml_category_delete, name='yaml_category_delete'),
    path('categories/rename/', views.yaml_category_rename, name='yaml_category_rename'),
    # Rules management
    path('rules/', views.yaml_rule_list, name='yaml_rule_list'),
    path('rules/add/', views.yaml_rule_add, name='yaml_rule_add'),
    path('rules/<int:idx>/edit/', views.yaml_rule_edit, name='yaml_rule_edit'),
    path('rules/<int:idx>/delete/', views.yaml_rule_delete, name='yaml_rule_delete'),
    path('rules/reclassify/', views.reclassify_all, name='reclassify_all'),
    path('rules/classify-unclassified/', views.classify_unclassified, name='classify_unclassified'),
    # User preferences
    path('preferences/transaction-columns/', views.save_transaction_columns, name='save_transaction_columns'),
]
