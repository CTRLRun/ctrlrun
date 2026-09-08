/* Browser acceptance checks against a Mintlify preview. All destinations are in memory. */
module.exports = async function verifyMedicalWorkbench(page, base = 'http://localhost:3000') {
  const checks = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/docs/demos/medical-affairs');
  const root = page.locator('#cr-medical-workbench');
  await root.waitFor();
  const click = async action => {
    await root.locator('[data-action="' + action + '"]').click();
    await root.locator('[data-step="0"]').waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('#cr-medical-workbench')?.getAttribute('aria-busy') === 'false', null, { timeout: 60000 });
  };
  const stage = async number => {
    await root.locator('[data-step="' + number + '"]').click();
    await page.waitForFunction(() => document.querySelector('#cr-medical-workbench')?.getAttribute('aria-busy') === 'false', null, { timeout: 60000 });
  };
  const expect = async (label, text) => {
    if (!(await root.innerText()).includes(text)) throw new Error(label + ': missing ' + text);
    checks.push(label);
  };
  await stage(2);
  await root.locator('[data-claim="1"]').click();
  await expect('Claim selection reveals its source passage', 'CLAIM REVIEW · C2');
  await click('toggle-claim');
  await stage(3);
  await expect('Unsupported claim fails Python fixture validation', '1 blocking issue');
  await stage(5);
  await click('release');
  await expect('Policy denies unsupported release', 'Validation blocked release');
  await expect('Denied release never writes', '0 simulated destination writes');
  await stage(3);
  await click('toggle-claim');
  await stage(4);
  await root.locator('[data-action="reviewed"]').check();
  await click('approve');
  await expect('Python grants current document approval', 'Version 3 approved');
  await click('edit');
  await stage(5);
  await click('release');
  await expect('Real approval hash rejects the changed document', 'Approval mismatch · release blocked');
  await expect('Mismatch never writes', '0 simulated destination writes');
  await click('go-review');
  await root.locator('[data-action="reviewed"]').check();
  await click('approve');
  await stage(5);
  await root.locator('[data-action="lose-reply"]').check();
  await click('release');
  await expect('Lost reply is ambiguous', 'Reply lost · outcome AMBIGUOUS');
  await click('release');
  await expect('A used approval cannot authorize a retry', 'Approval already used · retry blocked');
  await expect('Retry leaves destination writes at one', '1 simulated destination writes');
  await click('reconcile');
  await expect('Destination confirmation resolves without a second write', 'Destination confirms receipt');
  for (const action of ['download-brief', 'download-audit']) {
    const pending = page.waitForEvent('download');
    await click(action);
    const download = await pending;
    if (!download.suggestedFilename().startsWith('medical-evidence-')) throw new Error('Missing export');
    checks.push(action + ' produces a local file');
  }
  await click('restart');
  await stage(4);
  await root.locator('[data-action="reviewed"]').check();
  await click('approve');
  await stage(5);
  await click('release');
  await expect('New case supports a normal committed release', 'Reviewed brief released');
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const scheme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: scheme });
      for (let i = 0; i < 6; i++) {
        await stage(i);
        if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Overflow at ' + width + ', step ' + i);
      }
      checks.push('All six stages fit ' + width + 'px in ' + scheme);
    }
  }
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.emulateMedia({ colorScheme: 'light' });
  await stage(2);
  if (errors.length) throw new Error(errors.join('\n'));
  checks.push('No browser runtime errors');
  return checks;
};
