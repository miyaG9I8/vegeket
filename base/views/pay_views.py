from django.shortcuts import redirect
from django.views.generic import View, TemplateView
from django.conf import settings
from base.models import Item, Order
import stripe
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import serializers
import json
from django.contrib import messages

stripe.api_key = settings.STRIPE_API_SECRET_KEY

tax_rate = stripe.TaxRate.create(
    display_name='消費税',
    description='消費税',
    country='JP',
    jurisdiction='JP',
    percentage=settings.TAX_RATE * 100,
    inclusive=False, #外税を指定（内税の場合はTrue)
)

class PaySuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'pages/success.html'
    
    def get(self, request, *args, **kwargs):
        # 🔴 checkout_sessionで渡したクエリを取得
        order_id = request.GET.get('order_id')
        
        # 🔴 idと現userでOrderオブジェクトのリストを取得
        orders = Order.objects.filter(user=request.user, id=order_id)
        
        # 🔴 もし要素数が1でなければ以降に進まないようにここでreturn
        if len(orders) != 1:
            # 好みでリダイレクトやメッセージを表示してあげてもいいかもしれません。
            return super().get(request, *args, **kwargs)
        
        # 🔴 １つの要素を変数へ代入
        order = orders[0]

        order.is_confirmed = True  # 注文確定
        order.save()
        
        # 🔴 既にis_confirmed=Trueなら以降に進まないようにここでreturn
        if order.is_confirmed:
        # 好みでリダイレクトやメッセージを表示してあげてもいいかもしれません。
            return super().get(request, *args, **kwargs)
        
        #カート情報削除
        if 'cart' in request.session:
            del request.session['cart']
        
        return super().get(request, *args, **kwargs)


class PayCancelView(LoginRequiredMixin, TemplateView):
    template_name = 'pages/cancel.html'

    def get(self, request, *args, **kwargs):        
        # 現userの仮Orderオブジェクトのリストを取得
        orders = Order.objects.filter(user=request.user, is_confirmed=False)
        
        for order in orders:
            # 在庫数と販売数を元の状態に戻す
            for elem in json.loads(order.items):
                item = Item.objects.get(pk=elem['pk'])
                item.sold_count -= elem['quantity']
                item.stock += elem['quantity']
                item.save()
            
        # 仮オーダーを全て削除
        orders.delete()

        return super().get(request, *args, **kwargs)

        # is_confirmedがFalseであれば削除（仮オーダー削除）
        if not order.is_confirmed:
            order.delete()
            
        return super().get(request, *args, **kwargs)
    


def create_line_item(unit_amount, name, quantity):
    return {
        'price_data': {
            'currency': 'JPY',
            'unit_amount': unit_amount,
            'product_data': {'name': name, }
        },
        'quantity': quantity,
        'tax_rates': [tax_rate.id]
    }

#ユーザのプロファイルが埋まっていなければFalseを返す
def check_profile_filled(profile):
    if profile.name is None or profile.name == '':
        return False
    elif profile.zipcode is None or profile.zipcode == '':
        return False
    elif profile.prefecture is None or profile.prefecture == '':
        return False
    elif profile.city is None or profile.city == '':
        return False
    elif profile.address1 is None or profile.address1 == '':
        return False
    return True



class PayWithStripe(LoginRequiredMixin, View):

    def post(self, request, *args, **kwargs):
        # プロフィールが埋まっているかチェック
        if not check_profile_filled(request.user.profile):
            messages.error(request, '配送のためプロフィールを埋めてください。')
            return redirect('/profile/')
        
        cart = request.session.get('cart', None)
        if cart is None or len(cart) == 0:
            messages.error(request, 'カートが空です。')
            return redirect('/')
        
        items = [] #Orderも出るように追記

        line_items = []
        for item_pk, quantity in cart['items'].items():
            item = Item.objects.get(pk=item_pk)
            line_item = create_line_item(
                item.price, item.name, quantity)
            line_items.append(line_item)
            
            #Orderも出るように追記
            items.append({
                'pk': item.pk,
                'name': item.name,
                'image': str(item.image),
                'price': item.price,
                'quantity': quantity,
            })
            
            # 在庫をこの時点で引いておく、注文キャンセルの場合は在庫を戻す
            # 販売数も加算しておく
            item.stock -= quantity
            item.sold_count += quantity
            item.save()
            
        ## 仮注文を作成（is_confirmed=False)
        order = Order.objects.create(
            user=request.user,
            uid=request.user.pk,
            items=json.dumps(items),
            shipping=serializers.serialize("json", [request.user.profile]),
            amount=cart['total'],
            tax_included=cart['tax_included_total']
        )
            

        checkout_session = stripe.checkout.Session.create(
            customer_email=request.user.email,
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            #success_urlには、クエリで注文IDを渡しておく
            success_url=f'{settings.MY_URL}/pay/success/?order_id={order.pk}',
            cancel_url=f'{settings.MY_URL}/pay/cancel/',
        )
        return redirect(checkout_session.url)
    
