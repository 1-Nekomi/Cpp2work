//分钟数换算
#include<iostream>
using namespace std;
int main()
{
	cout<<"2026111308陈依琳"<<endl;
	int n;              //存放分钟数
	int hour;           //存放小时数
	int minute;         //存放分钟数
	cout<<"请输入分钟数：";
	cin>>n;
	hour=n/60;          //计算小时数
	minute=n%60;        //计算分钟数
	cout<<n<<"分钟可换算为："<<hour<<"小时"<<minute<<"分钟"<<endl;
	return 0;
}