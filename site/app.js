const copy = document.querySelector('#copy');
copy.addEventListener('click', async () => {
  const status = document.querySelector('#copy-status');
  try { await navigator.clipboard.writeText(document.querySelector('#install').textContent); status.textContent = 'Copied to clipboard.'; copy.textContent = 'Copied'; }
  catch { status.textContent = 'Couldn\'t reach the clipboard. Select the commands and copy them by hand.'; }
});
