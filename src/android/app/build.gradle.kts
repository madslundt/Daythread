plugins { id("com.android.application") }

android {
    namespace = "com.daythread.app"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.daythread.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildTypes { release { isMinifyEnabled = false } }
    lint { abortOnError = true }
}

dependencies {
    implementation("io.github.jaredmdobson:concentus:1.0.2")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}
