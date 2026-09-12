"""Thin order summary and quantity routes using the application's existing permissions."""
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, url_for

import order_production as progress
import production_order_views as views


class OrderProductionPages:
    def __init__(self, application, dependencies):
        self.d = dependencies
        application.add_url_rule('/admin/orders/groups/<int:anchor_id>', 'order_group_detail',
            self.d['permission_required']('orders_view')(self.order_detail))
        application.add_url_rule('/admin/production-followups/orders/<int:anchor_id>', 'order_production_detail',
            self.d['login_required'](self.production_detail))
        application.add_url_rule('/admin/production-followups/orders/<int:order_id>/start', 'start_order_production',
            self.d['permission_required']('production_followups_manage')(self.start), methods=['POST'])
        application.add_url_rule('/admin/production-followups/<int:followup_id>/processes/<int:step_id>/quantity',
            'set_order_process_quantity', self.d['permission_required']('production_followups_manage')(self.quantity), methods=['POST'])

    def context(self, conn):
        today = datetime.now().date()
        return dict(query=request.args.get('q', '').strip(), selected_customer=request.args.get('customer', '').strip(),
            sort=request.args.get('sort', 'ordered_at'), direction=request.args.get('direction', 'desc'),
            customers=self.d['get_order_customer_options'](conn), user_options=self.d['get_active_user_options'](conn),
            today=today.isoformat(), warning_until=(today + timedelta(days=10)).isoformat())

    def summary(self, production=False):
        with self.d['get_db']() as conn:
            context = self.context(conn)
            groups = views.matched_order_groups(views.sort_order_rows(views.fetch_order_rows(conn), context['sort'], context['direction']),
                context['query'], context['selected_customer'])
        return render_template('order_groups.html', groups=groups, production=production, **context)

    def group(self, conn, anchor_id):
        try:
            return views.fetch_order_group(conn, anchor_id)
        except LookupError:
            abort(404, description='订单明细不存在，请返回汇总刷新')

    def order_detail(self, anchor_id):
        with self.d['get_db']() as conn:
            group = self.group(conn, anchor_id)
            context = self.context(conn)
            group['rows'] = views.sort_order_rows(group['rows'], context['sort'], context['direction'])
        return render_template('order_group_detail.html', group=group, orders=group['rows'], **context)

    def production_detail(self, anchor_id):
        with self.d['get_db']() as conn:
            group = self.group(conn, anchor_id)
            items = [progress.decorate_order_processes(conn, row) for row in group['rows']]
            context = self.context(conn)
        return render_template('order_production_detail.html', group=group, items=items,
            production_followup_csrf_token=self.d['production_followup_csrf_token'](), **context)

    def start(self, order_id):
        self.d['require_production_followup_csrf']()
        try:
            with self.d['get_db']() as conn:
                progress.start_order_followup(conn, order_id, self.d['current_admin_username'](), datetime.now().isoformat(timespec='seconds'))
        except ValueError as error:
            abort(404, description=str(error))
        return redirect(url_for('order_production_detail', anchor_id=order_id, **self.return_filters()))

    def return_filters(self):
        return {key: request.form.get('filter_' + key, request.args.get(key, '')).strip()
                for key in ('q', 'customer', 'sort', 'direction')
                if request.form.get('filter_' + key, request.args.get(key, '')).strip()}

    def order_redirect(self, order_id=None):
        if order_id is not None:
            return redirect(url_for('order_group_detail', anchor_id=order_id, **self.return_filters()))
        return redirect(url_for('admin_orders', **self.return_filters()))

    def quantity(self, followup_id, step_id):
        self.d['require_production_followup_csrf']()
        with self.d['get_db']() as conn:
            order = progress.linked_order(conn, followup_id)
            if order is None:
                abort(404)
            try:
                progress.set_process_quantity(conn, followup_id, step_id,
                    request.form.get('quantity', ''), request.form.get('version', ''),
                    self.d['current_admin_username'](), datetime.now().isoformat(timespec='seconds'))
            except ValueError as error:
                flash(str(error), 'error')
            else:
                flash('累计完成数量已保存', 'success')
        return redirect(url_for('order_production_detail', anchor_id=order['id'], **self.return_filters()))
