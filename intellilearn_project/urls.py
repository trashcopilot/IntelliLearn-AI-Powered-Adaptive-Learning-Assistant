from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def favicon_view(request):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="IntelliLearn favicon">
    <rect width="64" height="64" rx="16" fill="#0f172a"/>
    <path d="M16 18h18c7 0 12 5 12 11 0 4-2 7-5 9 4 2 6 6 6 10 0 7-5 12-13 12H16V18zm12 17h6c4 0 6-2 6-5s-2-5-6-5h-6v10zm0 24h8c5 0 7-2 7-6s-2-6-7-6h-8v12z" fill="#38bdf8"/>
</svg>'''
        return HttpResponse(svg, content_type='image/svg+xml')

urlpatterns = [
        path('favicon.ico', favicon_view, name='favicon'),
    path('admin/', admin.site.urls),
    path('', include('users_app.urls')),
    path('content/', include('content_app.urls')),
    path('learning/', include('learning_app.urls')),
    path('analytics/', include('analytics_app.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
