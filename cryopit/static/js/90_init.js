// App start — runs once the whole script is parsed. Everything above is
// definitions; this is the only top-level execution block.
try{if(localStorage.getItem('cryopit-index-collapsed')==='1')toggleIndex(true);}catch(e){}
initTableEnterNavigation(); initClearableRadios();
buildInst(); initCollapse(); tick(); initSavedPitsFinder(); loadSavedPits(); restoreDraft(); initWorkspace(); drawMini(); refreshAttachUI();
if(typeof initAttachmentOutbox==='function')initAttachmentOutbox();
