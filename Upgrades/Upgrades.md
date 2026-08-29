
الابعاد 
موضوع التغذيه كيف ممكن يكون ؟ 

تخزين البيانات المقروءة 

\------ 

اضافات على الهاردوير 

تحكم يدوي 

حساس حرارة 

حساس اهتزاز 

لوحة تحكم
من ناحية هندسية وعملية، البحث عن "الأسهل" يجب ألا يخل بموثوقية البيانات المطلوبة لنجاح التوأم الرقمي. بناءً على البنية التي نناقشها، إليك الخيارات الأكثر استقراراً والأسهل من حيث التوفر وتوفر المكتبات البرمجية.

### أولاً: حساس الحرارة (DS18B20)

هذا هو المعيار الصناعي المصغر في مشاريع المتحكمات، وأوصي تحديداً بالنسخة ذات المسبار المعزول (Waterproof Probe).

* **لماذا هو الأسهل؟** يعتمد على بروتوكول `1-Wire`، مما يعني أنك تحتاج إلى منفذ بيانات (GPIO) واحد فقط على المتحكم لقراءة عدة حساسات متصلة على نفس الخط.
* **التركيب المادي:**
* لا تقم بتعليقه في الهواء. الحرارة التشغيلية تأتي من الاحتكاك أو الملفات الكهربائية.
* ضع قطرة من المعجون الحراري (Thermal Paste) على رأس الحساس النحاسي، ثم ثبته مباشرة على الهيكل المعدني للمحرك أو على كراسي التحميل (Bearings) باستخدام مرابط بلاستيكية قوية (Cable Ties) أو شريط لاصق حراري (Kapton Tape).


* **البرمجة والتوصيل:**
* تحتاج إلى ربط مقاومة `4.7k Ohm` بين سلك البيانات وسلك الجهد (VCC) لضمان استقرار الإشارة (Pull-up resistor).
* برمجياً، استخدم مكتبة `DallasTemperature` ومكتبة `OneWire`. القراءة تتم بسطر برمجي واحد يعطيك الحرارة بالدرجة المئوية مباشرة.



### ثانياً: حساس الاهتزاز (MPU6050)

يوجد في السوق حساس اهتزاز رخيص جداً (SW-420) يعطي قراءة رقمية (1 أو 0) إذا حدث اهتزاز، لكنه **عديم الفائدة** في التوأم الرقمي لأنه لا يقيس شدة الاهتزاز أو تردده. لذلك، الخيار العملي والأسهل برمجياً هو استخدام مقياس التسارع والجيروسكوب **MPU6050**.

* **لماذا هو الأسهل؟** بالرغم من أنه حساس معقد، إلا أن مكتباته البرمجية ضخمة جداً وتختصر عليك معادلات معقدة، ويعمل عبر بروتوكول `I2C`.
* **التركيب المادي (نقطة حرجة):**
* أي ارتخاء في تركيب الحساس سيجعله يقيس اهتزاز نفسه (الضوضاء الميكانيكية) وليس اهتزاز المحرك.
* يجب أن يُثبت **بشكل صلب (Rigid Mount)**. استخدم لاصق إيبوكسي (Epoxy) أو شريط لاصق مزدوج صناعي (3M VHB) لتثبيت اللوحة الإلكترونية الخاصة بالحساس على سطح مستوٍ وموازٍ لمحور دوران المحرك.


* **البرمجة والتوصيل:**
* يُوصل عبر منافذ `I2C` (منفذي SDA و SCL) في المتحكم.
* استخدم مكتبة `Adafruit_MPU6050`. بدلاً من الخوض في خوارزميات التردد (FFT) المعقدة في البداية، يمكنك برمجته لحساب التغير الكلي في التسارع (RMS Acceleration) في المحاور الثلاثة $(X, Y, Z)$، وإرسال هذه القيمة كـ "مؤشر لشدة الاهتزاز".



---

بما أن MPU6050 سيولد سيلاً مستمراً من بيانات التسارع عبر ناقل `I2C`، هل تخطط لإجراء الحسابات الرياضية (مثل حساب متوسط الاهتزاز) داخل المتحكم لتخفيف العبء، أم سترسل القراءات الخام فوراً لتتم معالجتها في مستوى أعلى (مثل Node-RED)؟
1. تكوين لوحة التحكم المادية (Hardware Layout)
ستحتاج إلى تثبيت 5 أزرار صناعية على واجهة صندوق التجميع:

زر التشغيل (Start Button - لون أخضر): من نوع (Normally Open - NO).

زر الإيقاف التشغيلي (Stop Button - لون أحمر عادي): من نوع (Normally Open - NO).

زر زيادة السرعة (+ Speed - لون أبيض أو أصفر): من نوع (Normally Open - NO).

زر خفض السرعة (- Speed - لون أسود أو أزرق): من نوع (Normally Open - NO).

مفتاح التوقف الطارئ (E-Stop Mushroom - لون أحمر بارز): مفتاح ميكانيكي يقفل عند الضغط، ويحتوي على نقطتي اتصال كما ناقشنا سابقاً (نقطة للمتحكم، ونقطة لقطع الطاقة عن مشغل المحرك).
Viewed app.js:76-90
Edited app.js

I have fixed both issues!

### 1. The Fixes:
* **Preventing Page Zoom:** Added an explicit event listener to the 3D canvas that intercepts the browser's default `wheel` scroll behavior and calls `e.preventDefault()`. This stops the browser from zooming or scrolling the entire page when you scroll your mouse wheel.
* **Camera Too Far:** Reduced the camera's default distance from the conveyor from `3.5` units to **`2.2` units** so it starts much closer.
* **Zoom Limits:** Set safety zoom boundaries (`lowerRadiusLimit` and `upperRadiusLimit`) so you cannot zoom inside the conveyor or zoom out infinitely.

---

### 2. Guide: How to Tune the Camera Settings Manually
You can find these settings at **line 80** of [app.js](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/app.js). Here is what each line does:

1. **Camera Starting Position and Distance:**
   ```javascript
   const camera = new BABYLON.ArcRotateCamera("Camera", -Math.PI / 2, Math.PI / 3, 2.2, BABYLON.Vector3.Zero(), scene);
   ```
   * **`2.2` (fourth parameter):** This is the **default distance** (radius) of the camera from the conveyor. Make this number smaller to start closer, or larger to start farther.
   * **`BABYLON.Vector3.Zero()` (fifth parameter):** This is the target coordinate `(0, 0, 0)` that the camera spins around.

2. **Zoom Speed:**
   ```javascript
   camera.wheelPrecision = 2.0;
   ```
   * A **lower** number (e.g. `1.0` or `1.5`) makes the zoom **faster/more sensitive**.
   * A **higher** number (e.g. `3.0` or `4.0`) makes the zoom **slower/more precise**.

3. **Zoom Constraints (Min & Max Distance):**
   ```javascript
   camera.lowerRadiusLimit = 1.0; // Zoom-in limit (how close you can get)
   camera.upperRadiusLimit = 5.0; // Zoom-out limit (how far away you can get)
   ```
   * Change `lowerRadiusLimit` to prevent the user from zooming through walls/meshes.
   * Change `upperRadiusLimit` to keep the conveyor within the user's field of view.
