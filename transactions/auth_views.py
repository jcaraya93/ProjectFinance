from django import forms
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import User
from .ratelimit import ratelimit


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'class': 'form-control', 'placeholder': 'Email', 'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'placeholder': 'Password',
    }))


class RegisterForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'class': 'form-control', 'placeholder': 'Email', 'autofocus': True,
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'placeholder': 'Password',
    }))
    password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'placeholder': 'Confirm password',
    }))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password') != cleaned.get('password_confirm'):
            raise forms.ValidationError('Passwords do not match.')
        if User.objects.filter(email=cleaned.get('email', '').lower()).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return cleaned


@ratelimit(key='login', rate='10/m', method='POST')
def login_view(request):
    if request.user.is_authenticated:
        return redirect('transactions:dashboard')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = authenticate(request, username=form.cleaned_data['email'], password=form.cleaned_data['password'])
        if user:
            login(request, user)
            next_url = request.GET.get('next', '/')
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = '/'
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid email or password.')
    return render(request, 'transactions/auth/login.html', {'form': form})


@ratelimit(key='register', rate='5/h', method='POST')
def register_view(request):
    if request.user.is_authenticated:
        return redirect('transactions:dashboard')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = User.objects.create_user(
            email=form.cleaned_data['email'].lower(),
            password=form.cleaned_data['password'],
        )
        user.create_default_categories()
        login(request, user)
        messages.success(request, 'Account created successfully.')
        return redirect('transactions:dashboard')
    return render(request, 'transactions/auth/register.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')
