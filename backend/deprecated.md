
backend/app/api/routes/order

# ─────────────────────────────────────────
# SİPARİŞ TAMAMLA (eski endpoint — deprecated, kaldırılabilir) DEPRECATED
# ─────────────────────────────────────────

@router.post("/{order_id}/complete-order", response_model=OrderStatusOut)
def complete_order(
    order_id: int,
    final_amount_input: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    '''
    DEPRECATED: Bunun yerine upload-final-invoice-ocr + final-invoice-submit akışı kullanılmalı.
    Bu endpoint fatura dosyası olmadan çalışır, yalnızca geriye dönük uyumluluk için bırakıldı.
    '''
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişler tamamlanabilir.")

    old_status         = order.status
    order.final_amount = final_amount_input
    order.updated_at   = datetime.now(timezone.utc)

    estimated = float(order.estimated_amount or 0)

    if abs(estimated - final_amount_input) > 0.01:
        order.status = OrderStatus.mismatch_review

        db.add(OrderStatusHistory(
            order_id=order.id, old_status=old_status,
            new_status=OrderStatus.mismatch_review, changed_by=current_user.id,
            note=(
                f"Tutar Uyuşmazlığı! İlk Tutar: {estimated} | "
                f"Final Tutar: {final_amount_input}"
            ),
        ))
        managers = db.query(User).filter(
            User.role == UserRole.manager, User.is_active == True
        ).all()
        for manager in managers:
            db.add(Notification(
                recipient_id=manager.id, order_id=order.id,
                type=NotifType.approval_needed,
                message=f'"{order.order_title}" ({order.order_number}) — tutar uyuşmazlığı! Müdür onayı bekleniyor.',
            ))
        log_action(db, AuditAction.amount_changed, current_user.id, order.id,
                   old_value={"estimated_amount": str(estimated)},
                   new_value={"final_amount": str(final_amount_input),
                              "status": OrderStatus.mismatch_review})
        db.commit()
        db.refresh(order)
        return order

    order.status       = OrderStatus.completed
    order.completed_at = datetime.now(timezone.utc)

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=OrderStatus.completed, changed_by=current_user.id,
    ))
    log_action(db, AuditAction.order_completed, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": OrderStatus.completed})

    db.commit()
    db.refresh(order)
    return order
