from django.contrib import admin
from django.urls import path, include
from haber_app import views  

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.dashboard, name='home'),  
]
