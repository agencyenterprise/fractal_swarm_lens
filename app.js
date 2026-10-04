const copy = document.querySelector('#copy');
copy.addEventListener('click', async () => {
  const status = document.querySelector('#copy-status');
  try { await navigator.clipboard.writeText(document.querySelector('#install').textContent); status.textContent = 'Commands copied.'; copy.textContent = 'Copied ✓'; }
  catch { status.textContent = 'Select the commands above and copy them manually.'; }
});
