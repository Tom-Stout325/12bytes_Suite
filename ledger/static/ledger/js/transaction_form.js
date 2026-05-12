
document.addEventListener('DOMContentLoaded', function () {

  const subcategory = document.getElementById('id_subcategory');
  const transportWrapper = document.getElementById('transport-wrapper');
  const vehicleWrapper = document.getElementById('vehicle-wrapper');
  const requirementsStatus = document.getElementById('requirements-status');
  const clearInvoiceBtn = document.getElementById('clear-invoice');
  const invoiceInput = document.getElementById('id_invoice_number');

  function updateUI() {
    if (!subcategory.value) {
      requirementsStatus.textContent = "Select a Sub-Category";
      return;
    }

    // For now this is simple logic placeholder.
    // You can later connect this to real SubCategory flags via JSON.
    requirementsStatus.textContent = "Review required fields";
    transportWrapper.classList.remove('d-none');
  }

  if (subcategory) {
    subcategory.addEventListener('change', updateUI);
  }

  if (clearInvoiceBtn && invoiceInput) {
    clearInvoiceBtn.addEventListener('click', function () {
      invoiceInput.value = "";
      invoiceInput.focus();
    });
  }

});
