from django import forms

class ProdutoForm(forms.Form):
    nome = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'class':'form-control'}))
    categoria = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class':'form-control'}))
    preco = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class':'form-control'}))
    disponivel = forms.BooleanField(required=False)
    descricao = forms.CharField(required=False, widget=forms.Textarea(attrs={'class':'form-control', 'rows':3}))
    # imagem será salva manualmente na view (request.FILES)
    imagem = forms.ImageField(required=False)

class AdicionalForm(forms.Form):
    nome = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'class':'form-control'}))
    preco = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class':'form-control'}))
    produto_id = forms.CharField(widget=forms.HiddenInput(), required=False)
